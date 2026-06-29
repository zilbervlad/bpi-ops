from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.models import (
    ChecklistOAMapping,
    ChecklistTemplateItem,
    DailyChecklist,
    DailyChecklistItem,
)


SECTION_ORDER = [
    "Before Open / Before 10:30",
    "During Dayshift",
    "3-O'Clock Restock",
    "Manager's Walk",
]


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
        "at_risk_points": 0.0,
        "completed_tasks": 0,
        "total_tasks": 0,
        "completion_percent": 0.0,
        "top_risks": [],
        "oa_items": {},
    }


def _sort_sections(section_names):
    known = [s for s in SECTION_ORDER if s in section_names]
    unknown = sorted([s for s in section_names if s not in SECTION_ORDER])
    return known + unknown


def build_execution_snapshot(store_number: str, checklist_date) -> dict[str, Any]:
    """
    Deterministic Doughy execution snapshot.

    This does not call AI.
    It converts checklist completion + OA mapping into an operations-readable summary:
    possible OA points, protected OA points, at-risk OA points, and top missed risks.
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
        "at_risk_points": 0.0,
        "completed_tasks": 0,
        "total_tasks": 0,
        "completion_percent": 0.0,
    }

    # Build from active template items so possible points are stable even if a daily checklist row is missing.
    for template_item in template_items:
        section_name = template_item.section_name or "(blank section)"
        section = sections.setdefault(section_name, _empty_section(section_name))

        daily_item = daily_items_by_template_id.get(template_item.id)
        completed = bool(daily_item.is_completed) if daily_item else False
        completed_at = daily_item.completed_at if daily_item else None

        section["total_tasks"] += 1
        totals["total_tasks"] += 1

        if completed:
            section["completed_tasks"] += 1
            totals["completed_tasks"] += 1

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
                    "at_risk_points": 0.0,
                },
            )
            section["oa_items"][oa_key]["possible_points"] += points

            if completed:
                section["protected_points"] += points
                totals["protected_points"] += points
                section["oa_items"][oa_key]["protected_points"] += points
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
        section["at_risk_points"] = round(
            max(section["possible_points"] - section["protected_points"], 0),
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
            oa_item["at_risk_points"] = round(
                max(oa_item["possible_points"] - oa_item["protected_points"], 0),
                2,
            )

        section["oa_items"] = sorted(
            section["oa_items"].values(),
            key=lambda row: (-row["at_risk_points"], row["oa_section"], row["oa_item_name"]),
        )

        section["top_risks"] = sorted(
            section["top_risks"],
            key=lambda row: (-row["points"], row["oa_section"], row["oa_item_name"], row["task"]),
        )[:8]

    totals["possible_points"] = round(totals["possible_points"], 2)
    totals["protected_points"] = round(totals["protected_points"], 2)
    totals["at_risk_points"] = round(
        max(totals["possible_points"] - totals["protected_points"], 0),
        2,
    )
    if totals["total_tasks"]:
        totals["completion_percent"] = round(
            totals["completed_tasks"] / totals["total_tasks"] * 100,
            1,
        )

    return {
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
