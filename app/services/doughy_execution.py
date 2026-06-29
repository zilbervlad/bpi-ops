from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models import (
    ChecklistOAMapping,
    ChecklistTemplateItem,
    DailyChecklist,
    DailyChecklistItem,
    IntegritySettings,
)


SECTION_ORDER = [
    "Before Open / Before 10:30",
    "During Dayshift",
    "3-O'Clock Restock",
    "Manager's Walk",
]

EASTERN_TZ = ZoneInfo("America/New_York")

SECTION_DUE_RULES = {
    "Before Open / Before 10:30": {
        "label": "Opening setup",
        "due_hour": 10,
        "due_minute": 30,
        "future_text": "Opening setup is not due yet.",
        "due_text": "Opening setup is due by 10:30 AM.",
        "overdue_text": "Opening setup is now past the 10:30 AM target.",
    },
    "During Dayshift": {
        "label": "During Dayshift",
        "due_hour": 14,
        "due_minute": 0,
        "future_text": "Dayshift work has not fully come due yet.",
        "due_text": "Dayshift work is active now.",
        "overdue_text": "Dayshift work should be under control by now.",
    },
    "3-O'Clock Restock": {
        "label": "3PM Reset",
        "due_hour": 15,
        "due_minute": 0,
        "future_text": "3PM Restock has not come due yet.",
        "due_text": "3PM Restock is due around 3:00 PM.",
        "overdue_text": "3PM Restock is now past the 3:00 PM target.",
    },
    "Manager's Walk": {
        "label": "Manager's Walk",
        "due_hour": 22,
        "due_minute": 0,
        "future_text": "Manager’s Walk protects tonight/tomorrow and has not come due yet.",
        "due_text": "Manager’s Walk should be completed as part of the closing reset.",
        "overdue_text": "Manager’s Walk is now in the closing window.",
    },
}

DEFAULT_INTEGRITY_RULES = {
    "Before Open / Before 10:30": {
        "burst_threshold": 4,
        "burst_window_seconds": 60,
        "questionable_ratio": 0.30,
        "fast_note_ratio": 0.50,
        "full_score_ratio": 0.70,
    },
    "Manager's Walk": {
        "burst_threshold": 3,
        "burst_window_seconds": 45,
        "questionable_ratio": 0.30,
        "fast_note_ratio": 0.50,
        "full_score_ratio": 0.70,
    },
    "During Dayshift": {
        "burst_threshold": 3,
        "burst_window_seconds": 45,
        "questionable_ratio": 0.30,
        "fast_note_ratio": 0.50,
        "full_score_ratio": 0.70,
    },
    "3-O'Clock Restock": {
        "burst_threshold": 3,
        "burst_window_seconds": 45,
        "questionable_ratio": 0.30,
        "fast_note_ratio": 0.50,
        "full_score_ratio": 0.70,
    },
}


def _date_value(value):
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError(f"Unsupported checklist_date value: {value!r}")



def _now_et() -> datetime:
    return datetime.now(EASTERN_TZ)


def _section_due_status(section_name: str, checklist_date: date) -> dict[str, Any]:
    """
    Time-aware section status for Doughy language.

    This does not decide whether a task is completed.
    It only tells Doughy whether a section is not due yet, active, or past target.
    """
    now = _now_et()
    today = now.date()

    rule = SECTION_DUE_RULES.get(section_name)
    if not rule:
        return {
            "status": "unknown",
            "label": section_name,
            "message": "No due-time rule is configured for this section.",
            "due_time": None,
        }

    due_dt = datetime(
        checklist_date.year,
        checklist_date.month,
        checklist_date.day,
        int(rule["due_hour"]),
        int(rule["due_minute"]),
        tzinfo=EASTERN_TZ,
    )

    if checklist_date < today:
        status = "past_due"
        message = rule["overdue_text"]
    elif checklist_date > today:
        status = "future_day"
        message = rule["future_text"]
    elif now < due_dt:
        status = "not_due"
        message = rule["future_text"]
    else:
        status = "due_now"
        message = rule["overdue_text"]

    return {
        "status": status,
        "label": rule["label"],
        "message": message,
        "due_time": due_dt.isoformat(),
    }

def _empty_section(section_name: str) -> dict[str, Any]:
    return {
        "section_name": section_name,
        "possible_points": 0.0,
        "protected_points": 0.0,
        "questionable_points": 0.0,
        "at_risk_points": 0.0,
        "completed_tasks": 0,
        "questionable_tasks": 0,
        "total_tasks": 0,
        "completion_percent": 0.0,
        "top_risks": [],
        "questionable_items": [],
        "integrity_flags": [],
        "timing_summary": None,
        "due_status": None,
        "oa_items": {},
    }


def _sort_sections(section_names):
    known = [s for s in SECTION_ORDER if s in section_names]
    unknown = sorted([s for s in section_names if s not in SECTION_ORDER])
    return known + unknown


def _build_integrity_rules() -> dict[str, dict[str, float]]:
    rules = {
        section: values.copy()
        for section, values in DEFAULT_INTEGRITY_RULES.items()
    }

    for row in IntegritySettings.query.all():
        section = row.integrity_section
        if not section:
            continue

        defaults = DEFAULT_INTEGRITY_RULES.get(section, {})
        rules[section] = {
            "burst_threshold": int(row.burst_threshold or defaults.get("burst_threshold", 3)),
            "burst_window_seconds": int(row.burst_window_seconds or defaults.get("burst_window_seconds", 45)),
            "questionable_ratio": float(row.low_score_ratio or defaults.get("questionable_ratio", 0.30)),
            "fast_note_ratio": float(row.medium_score_ratio or defaults.get("fast_note_ratio", 0.50)),
            "full_score_ratio": float(row.full_score_ratio or defaults.get("full_score_ratio", 0.70)),
        }

    return rules


def _find_questionable_daily_item_ids(
    daily_items: list[DailyChecklistItem],
) -> tuple[set[int], dict[str, list[str]], dict[str, dict[str, Any]]]:
    """
    Item-level integrity checks.

    Completed checklist items become questionable when:
    1. They are part of a suspicious burst.
    2. Their section was completed far faster than expected_minutes suggests.

    Returns:
    - questionable daily item ids
    - integrity flags by checklist section
    - timing summary by checklist section
    """
    rules = _build_integrity_rules()

    questionable_ids: set[int] = set()
    flags_by_section: dict[str, list[str]] = defaultdict(list)
    timing_by_section: dict[str, dict[str, Any]] = {}

    items_by_section: dict[str, list[DailyChecklistItem]] = defaultdict(list)

    for item in daily_items:
        if item.is_completed and item.completed_at:
            items_by_section[item.section_name or "(blank section)"].append(item)

    for section_name, items in items_by_section.items():
        section_rules = rules.get(section_name, {})

        burst_threshold = int(section_rules.get("burst_threshold") or 3)
        burst_window_seconds = int(section_rules.get("burst_window_seconds") or 45)

        # Timing ratios mirror the existing integrity idea:
        # 70%+ strong/full, 50%+ acceptable-fast, 30%+ weak, below 30% questionable.
        questionable_ratio = float(section_rules.get("questionable_ratio") or 0.30)
        fast_note_ratio = float(section_rules.get("fast_note_ratio") or 0.50)
        full_score_ratio = float(section_rules.get("full_score_ratio") or 0.70)

        completed_items = sorted(items, key=lambda row: row.completed_at)

        # 1. Burst detection: exact items in the burst become questionable.
        if len(completed_items) >= burst_threshold:
            for i in range(len(completed_items) - burst_threshold + 1):
                window_items = completed_items[i:i + burst_threshold]
                start_time = window_items[0].completed_at
                end_time = window_items[-1].completed_at

                if not start_time or not end_time:
                    continue

                elapsed_seconds = (end_time - start_time).total_seconds()

                if elapsed_seconds <= burst_window_seconds:
                    for questionable_item in window_items:
                        questionable_ids.add(questionable_item.id)

                    flags_by_section[section_name].append(
                        f"{burst_threshold} tasks completed in {int(elapsed_seconds)} seconds "
                        f"(burst threshold: {burst_threshold} in {burst_window_seconds}s)"
                    )

        # 2. Section timing: completed required items should take a believable amount of time.
        completed_required_items = [
            item for item in completed_items
            if item.is_required and (item.expected_minutes or 0) > 0
        ]

        expected_minutes = sum(item.expected_minutes or 0 for item in completed_required_items)

        if len(completed_required_items) >= 2 and expected_minutes > 0:
            first_completed = completed_required_items[0].completed_at
            last_completed = completed_required_items[-1].completed_at
            elapsed_minutes = (last_completed - first_completed).total_seconds() / 60

            ratio = elapsed_minutes / expected_minutes if expected_minutes else 0

            status = "believable"
            if ratio >= full_score_ratio:
                status = "strong"
            elif ratio >= fast_note_ratio:
                status = "acceptable_fast"
            elif ratio >= questionable_ratio:
                status = "weak_fast"
            else:
                status = "questionable"

            timing_by_section[section_name] = {
                "completed_required_tasks": len(completed_required_items),
                "expected_minutes": round(expected_minutes, 1),
                "elapsed_minutes": round(elapsed_minutes, 1),
                "ratio": round(ratio, 3),
                "status": status,
            }

            if ratio < questionable_ratio:
                for item in completed_required_items:
                    questionable_ids.add(item.id)

                flags_by_section[section_name].append(
                    f"{len(completed_required_items)} completed required tasks took "
                    f"{elapsed_minutes:.1f} minutes; expected about {expected_minutes:.1f} minutes "
                    f"({ratio:.0%} of expected)"
                )
            elif ratio < fast_note_ratio:
                flags_by_section[section_name].append(
                    f"{len(completed_required_items)} completed required tasks looked fast: "
                    f"{elapsed_minutes:.1f} minutes vs {expected_minutes:.1f} expected "
                    f"({ratio:.0%} of expected)"
                )

    return questionable_ids, flags_by_section, timing_by_section


def _build_doughy_read(snapshot: dict[str, Any]) -> dict[str, Any]:
    totals = snapshot.get("totals", {})
    sections = snapshot.get("sections", [])

    possible = totals.get("possible_points", 0) or 0
    protected = totals.get("protected_points", 0) or 0
    questionable = totals.get("questionable_points", 0) or 0
    at_risk = totals.get("at_risk_points", 0) or 0

    current_focus = []
    future_focus = []
    review_focus = []
    section_reads = []

    for section in sections:
        name = section.get("section_name")
        s_possible = section.get("possible_points", 0) or 0
        s_protected = section.get("protected_points", 0) or 0
        s_questionable = section.get("questionable_points", 0) or 0
        s_at_risk = section.get("at_risk_points", 0) or 0
        timing = section.get("timing_summary")
        flags = section.get("integrity_flags") or []
        due_status = section.get("due_status") or {}
        due_state = due_status.get("status")

        if not s_possible:
            continue

        if s_questionable > 0:
            status = "needs_review"
        elif due_state in ("not_due", "future_day") and s_protected == 0:
            status = "pending"
        elif s_at_risk == 0 and s_protected >= s_possible:
            status = "protected"
        elif s_protected > 0:
            status = "partially_protected"
        elif s_at_risk >= s_possible:
            status = "at_risk"
        else:
            status = "review"

        section_reads.append({
            "section_name": name,
            "status": status,
            "due_status": due_status,
            "protected_points": s_protected,
            "questionable_points": s_questionable,
            "at_risk_points": s_at_risk,
            "possible_points": s_possible,
            "timing_summary": timing,
            "integrity_flags": flags,
        })

        if s_questionable > 0:
            review_focus.append(
                f"{name} has {s_questionable:g} checked points that are not fully verified due to timing."
            )

        if due_state in ("not_due", "future_day"):
            if s_at_risk > 0 and s_protected == 0:
                future_focus.append(
                    f"{name} is pending: {due_status.get('message')}"
                )
            elif s_at_risk > 0:
                future_focus.append(
                    f"{name} is partly started, with {s_at_risk:g} points still pending before it comes due."
                )
            continue

        if s_at_risk > 0:
            top_risks = section.get("top_risks") or []
            if top_risks:
                top = top_risks[0]
                current_focus.append(
                    f"{name} has {s_at_risk:g} OA-mapped points currently at risk. Top risk: {top.get('task')}."
                )
            else:
                current_focus.append(
                    f"{name} has {s_at_risk:g} OA-mapped points currently at risk."
                )

    headline = "Execution snapshot ready."

    if review_focus and current_focus:
        headline = "Some work is protected, but timing and current risks need review."
    elif review_focus:
        headline = "Some checked work needs timing review."
    elif current_focus and protected > 0:
        headline = "Some OA points are protected, but current risks remain."
    elif current_focus:
        headline = "Current OA-mapped work still needs attention."
    elif protected >= possible and possible > 0:
        headline = "All currently due OA-mapped points are protected."
    elif future_focus and not current_focus:
        headline = "Future checklist sections are still pending."

    summary = (
        f"{snapshot.get('store_number')} protected {protected:g} of {possible:g} OA-mapped points. "
        f"{questionable:g} points are checked but not fully verified, and {at_risk:g} points are not protected yet."
    )

    focus_items = []
    focus_items.extend(review_focus[:3])
    focus_items.extend(current_focus[:4])
    focus_items.extend(future_focus[:3])

    safe_language = [
        "Say 'checked but not fully verified' instead of 'fake'.",
        "Say 'timing looks questionable' instead of accusing a manager.",
        "Treat burst and expected-time flags as review signals, not proof of misconduct.",
        "Separate current risks from sections that are not due yet.",
        "Focus on recovery and supervisor review.",
    ]

    return {
        "headline": headline,
        "summary": summary,
        "focus_items": focus_items[:7],
        "current_focus": current_focus,
        "future_focus": future_focus,
        "review_focus": review_focus,
        "sections": section_reads,
        "safe_language": safe_language,
    }

def build_execution_snapshot(store_number: str, checklist_date) -> dict[str, Any]:
    """
    Deterministic Doughy execution snapshot.

    This does not call AI.

    Item states:
    - Protected: completed and not part of a suspicious integrity burst
    - Questionable: completed, but part of a suspicious integrity burst
    - At Risk: not completed
    """
    checklist_date = _date_value(checklist_date)

    checklist = (
        DailyChecklist.query
        .filter_by(store_number=str(store_number), checklist_date=checklist_date)
        .order_by(DailyChecklist.id.desc())
        .first()
    )

    template_items = (
        ChecklistTemplateItem.query
        .filter_by(is_active=True)
        .order_by(ChecklistTemplateItem.section_name.asc(), ChecklistTemplateItem.sort_order.asc())
        .all()
    )

    mappings = (
        ChecklistOAMapping.query
        .filter_by(is_active=True)
        .all()
    )

    mappings_by_template_id = defaultdict(list)
    for mapping in mappings:
        if mapping.checklist_template_item_id:
            mappings_by_template_id[mapping.checklist_template_item_id].append(mapping)

    daily_items_by_template_id = {}
    daily_items = []

    if checklist:
        daily_items = (
            DailyChecklistItem.query
            .filter_by(daily_checklist_id=checklist.id)
            .all()
        )
        for item in daily_items:
            if item.template_item_id:
                daily_items_by_template_id[item.template_item_id] = item

    integrity_result = _find_questionable_daily_item_ids(daily_items)
    if len(integrity_result) == 3:
        questionable_daily_item_ids, flags_by_section, timing_by_section = integrity_result
    else:
        questionable_daily_item_ids, flags_by_section = integrity_result
        timing_by_section = {}

    all_section_names = set(SECTION_ORDER)
    for item in template_items:
        all_section_names.add(item.section_name or "(blank section)")

    sections = {
        section_name: _empty_section(section_name)
        for section_name in _sort_sections(all_section_names)
    }

    for section_name, section in sections.items():
        section["due_status"] = _section_due_status(section_name, checklist_date)

    totals = {
        "possible_points": 0.0,
        "protected_points": 0.0,
        "questionable_points": 0.0,
        "at_risk_points": 0.0,
        "completed_tasks": 0,
        "questionable_tasks": 0,
        "total_tasks": 0,
        "completion_percent": 0.0,
    }

    for section_name, flags in flags_by_section.items():
        section = sections.setdefault(section_name, _empty_section(section_name))
        section["integrity_flags"].extend(flags)

    for section_name, timing in timing_by_section.items():
        section = sections.setdefault(section_name, _empty_section(section_name))
        section["timing_summary"] = timing

    # Build from active template items so possible points are stable even if a daily checklist row is missing.
    for template_item in template_items:
        section_name = template_item.section_name or "(blank section)"
        section = sections.setdefault(section_name, _empty_section(section_name))

        daily_item = daily_items_by_template_id.get(template_item.id)
        completed = bool(daily_item.is_completed) if daily_item else False
        completed_at = daily_item.completed_at if daily_item else None
        is_questionable = bool(daily_item and daily_item.id in questionable_daily_item_ids)

        section["total_tasks"] += 1
        totals["total_tasks"] += 1

        if completed:
            section["completed_tasks"] += 1
            totals["completed_tasks"] += 1

        if is_questionable:
            section["questionable_tasks"] += 1
            totals["questionable_tasks"] += 1

        item_mappings = mappings_by_template_id.get(template_item.id, [])

        for mapping in item_mappings:
            points = float(mapping.oa_points or 0)
            if points <= 0:
                continue

            oa_section = mapping.oa_section or "(blank OA section)"
            oa_item_name = mapping.oa_item_name or "(blank OA item)"

            section["possible_points"] += points
            totals["possible_points"] += points

            oa_key = f"{oa_section} / {oa_item_name}"
            section["oa_items"].setdefault(
                oa_key,
                {
                    "oa_section": oa_section,
                    "oa_item_name": oa_item_name,
                    "possible_points": 0.0,
                    "protected_points": 0.0,
                    "questionable_points": 0.0,
                    "at_risk_points": 0.0,
                },
            )
            section["oa_items"][oa_key]["possible_points"] += points

            if completed and not is_questionable:
                section["protected_points"] += points
                totals["protected_points"] += points
                section["oa_items"][oa_key]["protected_points"] += points
            elif completed and is_questionable:
                section["questionable_points"] += points
                totals["questionable_points"] += points
                section["oa_items"][oa_key]["questionable_points"] += points
                section["questionable_items"].append(
                    {
                        "task": template_item.task_text,
                        "oa_section": oa_section,
                        "oa_item_name": oa_item_name,
                        "points": points,
                        "completed": True,
                        "completed_at": completed_at.isoformat() if completed_at else None,
                        "is_required": bool(template_item.is_required),
                        "reason": "Completed inside suspicious timing burst",
                    }
                )
            else:
                section["top_risks"].append(
                    {
                        "task": template_item.task_text,
                        "oa_section": oa_section,
                        "oa_item_name": oa_item_name,
                        "points": points,
                        "completed": False,
                        "completed_at": completed_at.isoformat() if completed_at else None,
                        "is_required": bool(template_item.is_required),
                    }
                )

    # Finalize sections.
    for section in sections.values():
        section["possible_points"] = round(section["possible_points"], 2)
        section["protected_points"] = round(section["protected_points"], 2)
        section["questionable_points"] = round(section["questionable_points"], 2)
        section["at_risk_points"] = round(
            max(section["possible_points"] - section["protected_points"] - section["questionable_points"], 0),
            2,
        )

        if section["total_tasks"]:
            section["completion_percent"] = round(
                section["completed_tasks"] / section["total_tasks"] * 100,
                1,
            )

        for oa_item in section["oa_items"].values():
            oa_item["possible_points"] = round(oa_item["possible_points"], 2)
            oa_item["protected_points"] = round(oa_item["protected_points"], 2)
            oa_item["questionable_points"] = round(oa_item["questionable_points"], 2)
            oa_item["at_risk_points"] = round(
                max(
                    oa_item["possible_points"]
                    - oa_item["protected_points"]
                    - oa_item["questionable_points"],
                    0,
                ),
                2,
            )

        section["oa_items"] = sorted(
            section["oa_items"].values(),
            key=lambda row: (-row["at_risk_points"], -row["questionable_points"], row["oa_section"], row["oa_item_name"]),
        )

        section["top_risks"] = sorted(
            section["top_risks"],
            key=lambda row: (-row["points"], row["oa_section"], row["oa_item_name"], row["task"]),
        )[:8]

        section["questionable_items"] = sorted(
            section["questionable_items"],
            key=lambda row: (-row["points"], row["oa_section"], row["oa_item_name"], row["task"]),
        )[:8]

    totals["possible_points"] = round(totals["possible_points"], 2)
    totals["protected_points"] = round(totals["protected_points"], 2)
    totals["questionable_points"] = round(totals["questionable_points"], 2)
    totals["at_risk_points"] = round(
        max(totals["possible_points"] - totals["protected_points"] - totals["questionable_points"], 0),
        2,
    )
    if totals["total_tasks"]:
        totals["completion_percent"] = round(
            totals["completed_tasks"] / totals["total_tasks"] * 100,
            1,
        )

    snapshot = {
        "store_number": str(store_number),
        "checklist_date": checklist_date.isoformat(),
        "daily_checklist_id": checklist.id if checklist else None,
        "manager_on_duty": checklist.manager_on_duty if checklist else None,
        "opening_manager": getattr(checklist, "opening_manager", None) if checklist else None,
        "closing_manager": getattr(checklist, "closing_manager", None) if checklist else None,
        "status": checklist.status if checklist else "missing",
        "percent_complete": checklist.percent_complete if checklist else None,
        "integrity_score": checklist.integrity_score if checklist else None,
        "integrity_possible": getattr(checklist, "integrity_possible", None) if checklist else None,
        "totals": totals,
        "sections": [sections[name] for name in _sort_sections(sections.keys())],
    }
    snapshot["doughy_read"] = _build_doughy_read(snapshot)
    return snapshot
