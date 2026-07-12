from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from app.models import (
    CashLog,
    DailyChecklist,
    MaintenanceTicket,
    NightlyNumbersReport,
    Store,
    SVRReport,
    VerificationReport,
    WeeklyFocusItem,
)
from app.services.doughy_execution import build_execution_snapshot


TERMINAL_MAINTENANCE_STATUSES = {
    "complete",
    "completed",
    "verified",
    "cancelled",
    "canceled",
    "closed",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _iso(value: Any) -> str | None:
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            pass

    return date.today()


def visible_store_numbers(user_context: dict[str, Any]) -> set[str]:
    """
    Central conservative store-scope rule for Doughy's read-only access.

    This does not grant route access. It limits what the data gateway may read.
    """
    role = str(user_context.get("role") or "").strip().lower()
    user_area = str(user_context.get("user_area") or "").strip()
    user_store = str(user_context.get("user_store") or "").strip()

    query = Store.query.filter(Store.is_active == True)

    if role in {"admin", "maintenance"}:
        stores = query.all()

    elif role == "supervisor":
        if not user_area:
            stores = []
        else:
            stores = query.filter(
                Store.area_name == user_area
            ).all()

    elif role in {
        "manager",
        "general_manager",
        "tm",
    }:
        if not user_store:
            stores = []
        else:
            stores = query.filter(
                Store.store_number == user_store
            ).all()

    else:
        stores = []

    return {
        str(store.store_number)
        for store in stores
        if store.store_number
    }


def _serialize_maintenance(ticket: MaintenanceTicket) -> dict[str, Any]:
    created_date = None
    age_days = None

    if ticket.created_at:
        created_date = (
            ticket.created_at.date()
            if isinstance(ticket.created_at, datetime)
            else ticket.created_at
        )
        age_days = max((date.today() - created_date).days, 0)

    return {
        "id": ticket.id,
        "store_number": ticket.store_number,
        "title": ticket.title,
        "details": _clean_text(ticket.details),
        "status": ticket.status,
        "priority": ticket.priority,
        "assigned_to": ticket.assigned_to,
        "scheduled_date": _iso(ticket.scheduled_date),
        "created_at": _iso(ticket.created_at),
        "age_days": age_days,
        "source_type": ticket.source_type,
        "svr_report_id": ticket.svr_report_id,
    }


def _serialize_nightly(report: NightlyNumbersReport) -> dict[str, Any]:
    labor_variance = None

    if (
        report.variable_labor is not None
        and report.labor_goal is not None
    ):
        labor_variance = round(
            report.variable_labor - report.labor_goal,
            2,
        )

    return {
        "id": report.id,
        "store_number": report.store_number,
        "report_date": _iso(report.report_date),
        "manager_name": report.manager_name,
        "royalty_sales": report.royalty_sales,
        "variable_labor": report.variable_labor,
        "labor_goal": report.labor_goal,
        "labor_variance_to_goal": labor_variance,
        "food_variance": report.food_variance,
        "food_variance_details": _clean_text(
            report.food_variance_details
        ),
        "adt": report.adt,
        "adt_reason": _clean_text(report.adt_reason),
        "load_time": report.load_time,
        "bad_orders": _clean_text(report.bad_orders),
        "cash_diff": report.cash_diff,
        "invoices_transfers_checked": bool(
            report.invoices_transfers_checked
        ),
        "food_order_placed": bool(report.food_order_placed),
    }


def _serialize_svr(report: SVRReport) -> dict[str, Any]:
    observations = []

    for value in sorted(
        report.values,
        key=lambda row: (row.sort_order, row.id),
    ):
        text = _clean_text(value.value_text)

        if not text:
            continue

        observations.append({
            "field_key": value.field_key,
            "field_label": value.field_label,
            "value_text": text,
        })

    return {
        "id": report.id,
        "store_number": report.store_number,
        "visit_date": _iso(report.visit_date),
        "manager_on_duty": report.manager_on_duty,
        "supervisor_name": report.supervisor_name,
        "observations": observations[:30],
    }


def _serialize_verification(
    report: VerificationReport,
) -> dict[str, Any]:
    values = []

    for value in sorted(
        report.values,
        key=lambda row: (row.sort_order, row.id),
    ):
        text = _clean_text(value.value_text)

        if not text:
            continue

        values.append({
            "field_key": value.field_key,
            "field_label": value.field_label,
            "value_text": text,
        })

    return {
        "id": report.id,
        "store_number": report.store_number,
        "report_date": _iso(report.report_date),
        "supervisor_name": report.supervisor_name,
        "responses": values[:30],
    }


def _store_context(
    store_number: str,
    business_date: date,
) -> dict[str, Any]:
    checklist = (
        DailyChecklist.query
        .filter_by(
            store_number=store_number,
            checklist_date=business_date,
        )
        .order_by(DailyChecklist.id.desc())
        .first()
    )

    checklist_context = None

    if checklist:
        checklist_context = build_execution_snapshot(
            store_number,
            business_date,
        )

    maintenance_rows = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number == store_number
        )
        .order_by(
            MaintenanceTicket.created_at.asc(),
            MaintenanceTicket.id.asc(),
        )
        .all()
    )

    active_maintenance = [
        ticket
        for ticket in maintenance_rows
        if str(ticket.status or "").strip().lower()
        not in TERMINAL_MAINTENANCE_STATUSES
    ]

    recent_svrs = (
        SVRReport.query
        .filter(SVRReport.store_number == store_number)
        .order_by(
            SVRReport.visit_date.desc(),
            SVRReport.created_at.desc(),
            SVRReport.id.desc(),
        )
        .limit(4)
        .all()
    )

    recent_nightly = (
        NightlyNumbersReport.query
        .filter(
            NightlyNumbersReport.store_number == store_number
        )
        .order_by(
            NightlyNumbersReport.report_date.desc(),
            NightlyNumbersReport.id.desc(),
        )
        .limit(7)
        .all()
    )

    recent_verifications = (
        VerificationReport.query
        .filter(
            VerificationReport.store_number == store_number
        )
        .order_by(
            VerificationReport.report_date.desc(),
            VerificationReport.created_at.desc(),
            VerificationReport.id.desc(),
        )
        .limit(4)
        .all()
    )

    open_focus = (
        WeeklyFocusItem.query
        .filter(
            WeeklyFocusItem.store_number == store_number,
            WeeklyFocusItem.is_completed == False,
        )
        .order_by(
            WeeklyFocusItem.created_at.asc(),
            WeeklyFocusItem.id.asc(),
        )
        .limit(20)
        .all()
    )

    recent_cash = (
        CashLog.query
        .filter(CashLog.store_number == store_number)
        .order_by(
            CashLog.log_date.desc(),
            CashLog.created_at.desc(),
            CashLog.id.desc(),
        )
        .limit(10)
        .all()
    )

    status_counts = Counter(
        str(ticket.status or "unknown")
        for ticket in active_maintenance
    )

    return {
        "store_number": store_number,
        "business_date": business_date.isoformat(),
        "checklist": checklist_context,
        "maintenance": {
            "active_count": len(active_maintenance),
            "status_counts": dict(status_counts),
            "oldest_active": [
                _serialize_maintenance(ticket)
                for ticket in active_maintenance[:8]
            ],
        },
        "svr": {
            "recent_reports": [
                _serialize_svr(report)
                for report in recent_svrs
            ],
        },
        "nightly_numbers": {
            "recent_reports": [
                _serialize_nightly(report)
                for report in recent_nightly
            ],
        },
        "verification": {
            "recent_reports": [
                _serialize_verification(report)
                for report in recent_verifications
            ],
        },
        "weekly_focus": {
            "open_count": len(open_focus),
            "items": [
                {
                    "id": item.id,
                    "item_type": item.item_type,
                    "item_text": item.item_text,
                    "source_type": item.source_type,
                    "svr_report_id": item.svr_report_id,
                    "created_at": _iso(item.created_at),
                }
                for item in open_focus
            ],
        },
        "cash": {
            "recent_logs": [
                {
                    "id": row.id,
                    "log_date": _iso(row.log_date),
                    "shift_type": row.shift_type,
                    "cash_over_short": row.cash_over_short,
                    "manager_name": row.manager_name,
                }
                for row in recent_cash
            ],
        },
    }


def _scope_rollup(
    store_numbers: set[str],
    business_date: date,
) -> dict[str, Any]:
    if not store_numbers:
        return {
            "store_count": 0,
            "business_date": business_date.isoformat(),
        }

    checklists = (
        DailyChecklist.query
        .filter(
            DailyChecklist.store_number.in_(store_numbers),
            DailyChecklist.checklist_date == business_date,
        )
        .all()
    )

    latest_checklist_by_store = {}

    for row in sorted(
        checklists,
        key=lambda item: item.id,
        reverse=True,
    ):
        latest_checklist_by_store.setdefault(
            str(row.store_number),
            row,
        )

    maintenance = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(store_numbers)
        )
        .all()
    )

    active_maintenance = [
        ticket
        for ticket in maintenance
        if str(ticket.status or "").strip().lower()
        not in TERMINAL_MAINTENANCE_STATUSES
    ]

    week_start = business_date - timedelta(
        days=business_date.weekday()
    )
    week_end = week_start + timedelta(days=6)

    weekly_svrs = (
        SVRReport.query
        .filter(
            SVRReport.store_number.in_(store_numbers),
            SVRReport.visit_date >= week_start,
            SVRReport.visit_date <= week_end,
        )
        .all()
    )

    weekly_verifications = (
        VerificationReport.query
        .filter(
            VerificationReport.store_number.in_(store_numbers),
            VerificationReport.report_date >= week_start,
            VerificationReport.report_date <= week_end,
        )
        .all()
    )

    nightly_rows = (
        NightlyNumbersReport.query
        .filter(
            NightlyNumbersReport.store_number.in_(store_numbers),
            NightlyNumbersReport.report_date == business_date,
        )
        .all()
    )

    checklist_stores = set(latest_checklist_by_store.keys())
    svr_stores = {
        str(report.store_number)
        for report in weekly_svrs
    }
    verification_stores = {
        str(report.store_number)
        for report in weekly_verifications
    }
    nightly_stores = {
        str(report.store_number)
        for report in nightly_rows
    }

    low_checklists = []

    for store_number, row in latest_checklist_by_store.items():
        if (
            (row.percent_complete or 0) < 80
            or (row.integrity_score or 0) < 70
        ):
            low_checklists.append({
                "store_number": store_number,
                "percent_complete": row.percent_complete,
                "integrity_score": row.integrity_score,
                "status": row.status,
            })

    maintenance_by_store = Counter(
        str(ticket.store_number)
        for ticket in active_maintenance
    )

    return {
        "business_date": business_date.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "store_count": len(store_numbers),
        "checklist": {
            "submitted_count": len(checklist_stores),
            "missing_stores": sorted(
                store_numbers - checklist_stores
            ),
            "needs_attention": sorted(
                low_checklists,
                key=lambda row: (
                    row.get("integrity_score") or 0,
                    row.get("percent_complete") or 0,
                ),
            )[:12],
        },
        "maintenance": {
            "active_count": len(active_maintenance),
            "stores_by_active_count": [
                {
                    "store_number": store_number,
                    "active_count": count,
                }
                for store_number, count
                in maintenance_by_store.most_common(12)
            ],
        },
        "svr": {
            "submitted_this_week": len(svr_stores),
            "missing_stores": sorted(
                store_numbers - svr_stores
            ),
        },
        "verification": {
            "submitted_this_week": len(verification_stores),
            "missing_stores": sorted(
                store_numbers - verification_stores
            ),
        },
        "nightly_numbers": {
            "submitted_count": len(nightly_stores),
            "missing_stores": sorted(
                store_numbers - nightly_stores
            ),
            "reports": [
                _serialize_nightly(report)
                for report in nightly_rows[:30]
            ],
        },
    }


def build_doughy_context(
    *,
    user_context: dict[str, Any],
    page_context: dict[str, Any],
    requested_store: str | None = None,
    requested_date: Any = None,
) -> dict[str, Any]:
    business_date = _parse_date(requested_date)
    allowed_stores = visible_store_numbers(user_context)

    store = str(requested_store or "").strip() or None

    if store and store not in allowed_stores:
        return {
            "ok": False,
            "error": "Store is outside the user's visible scope.",
            "page": page_context,
            "scope": {
                "role": user_context.get("role"),
                "visible_store_count": len(allowed_stores),
            },
        }

    result = {
        "ok": True,
        "mode": "read_only_bpi_data_gateway",
        "page": page_context,
        "scope": {
            "role": user_context.get("role"),
            "user_area": user_context.get("user_area"),
            "user_store": user_context.get("user_store"),
            "visible_store_count": len(allowed_stores),
            "visible_store_numbers": sorted(allowed_stores),
        },
        "requested": {
            "store": store,
            "business_date": business_date.isoformat(),
        },
    }

    if store:
        result["store_context"] = _store_context(
            store,
            business_date,
        )
    else:
        result["scope_rollup"] = _scope_rollup(
            allowed_stores,
            business_date,
        )

    return result
