from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

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

    section_reads = []
    focus_items = []

    for section in sections:
        name = section.get("section_name")
        s_possible = section.get("possible_points", 0) or 0
        s_protected = section.get("protected_points", 0) or 0
        s_questionable = section.get("questionable_points", 0) or 0
        s_at_risk = section.get("at_risk_points", 0) or 0
        timing = section.get("timing_summary")
        flags = section.get("integrity_flags") or []

        if not s_possible:
            continue

        status = "not_started"
        if s_questionable > 0:
            status = "needs_review"
        elif s_at_risk == 0 and s_protected >= s_possible:
            status = "protected"
        elif s_protected > 0:
            status = "partially_protected"
        elif s_at_risk >= s_possible:
            status = "fully_at_risk"

        section_read = {
            "section_name": name,
            "status": status,
            "protected_points": s_protected,
            "questionable_points": s_questionable,
            "at_risk_points": s_at_risk,
            "possible_points": s_possible,
            "timing_summary": timing,
            "integrity_flags": flags,
        }
        section_reads.append(section_read)

        if s_questionable > 0:
            focus_items.append(
                f"{name} has {s_questionable:g} checked points that are not fully verified due to timing."
            )
        if s_at_risk > 0:
            top_risks = section.get("top_risks") or []
            if top_risks:
                top = top_risks[0]
                focus_items.append(
                    f"{name} still has {s_at_risk:g} OA-mapped points at risk. Top risk: {top.get('task')}."
                )
            else:
                focus_items.append(
                    f"{name} still has {s_at_risk:g} OA-mapped points at risk."
                )

    headline = "Execution snapshot ready."

    if questionable > 0 and at_risk > 0:
        headline = "Some work is protected, but timing and open risks need review."
    elif questionable > 0:
        headline = "Some checked work needs timing review."
    elif at_risk > 0 and protected > 0:
        headline = "Some OA points are protected, but risks remain."
    elif at_risk >= possible and possible > 0:
        headline = "No OA-mapped points are protected yet."
    elif protected >= possible and possible > 0:
        headline = "All OA-mapped points are protected."

    summary = (
        f"{snapshot.get('store_number')} protected {protected:g} of {possible:g} OA-mapped points. "
        f"{questionable:g} points are checked but not fully verified, and {at_risk:g} points remain at risk."
    )

    safe_language = [
        "Say 'checked but not fully verified' instead of 'fake'.",
        "Say 'timing looks questionable' instead of accusing a manager.",
        "Treat burst and expected-time flags as review signals, not proof of misconduct.",
        "Focus on what needs recovery or supervisor review.",
    ]

    return {
        "headline": headline,
        "summary": summary,
        "focus_items": focus_items[:6],
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
