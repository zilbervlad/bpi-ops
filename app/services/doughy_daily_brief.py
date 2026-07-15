from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    ChecklistException,
    DailyChecklist,
    DoughyDailyBriefLog,
    DWPRecord,
    HRDocument,
    HRDocumentRecipient,
    MaintenanceTicket,
    NightlyNumbersReport,
    Store,
    SVRReport,
    User,
)
from app.services.doughy_ai_service import ask_doughy_ai
from app.services.email_service import send_email


APP_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

RECIPIENT_ROLES = {
    "admin",
    "hr",
    "supervisor",
    "general_manager",
}


def now_et() -> datetime:
    return datetime.now(APP_TZ)


def completed_ops_date(reference_time: datetime | None = None):
    current = reference_time or now_et()

    current_ops_date = (
        current.date() - timedelta(days=1)
        if current.hour < 5
        else current.date()
    )

    return current_ops_date - timedelta(days=1)


def ops_timestamp_window_utc_naive(brief_date):
    start_et = datetime.combine(
        brief_date,
        time(hour=5),
        tzinfo=APP_TZ,
    )
    end_et = start_et + timedelta(days=1)

    start_utc = start_et.astimezone(UTC_TZ).replace(tzinfo=None)
    end_utc = end_et.astimezone(UTC_TZ).replace(tzinfo=None)

    return start_utc, end_utc


def visible_stores_for_user(user: User) -> list[Store]:
    role = (user.role or "").strip().lower()

    query = Store.query.filter(Store.is_active.is_(True))

    if role in {"admin", "hr"}:
        return query.order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        if not user.area_name:
            return []

        return (
            query
            .filter(Store.area_name == user.area_name)
            .order_by(Store.store_number.asc())
            .all()
        )

    if role == "general_manager":
        if not user.store_number:
            return []

        return (
            query
            .filter(Store.store_number == str(user.store_number))
            .order_by(Store.store_number.asc())
            .all()
        )

    return []


def recipient_scope_label(user: User, stores: list[Store]) -> str:
    role = (user.role or "").strip().lower()

    if role in {"admin", "hr"}:
        return "All active BPI stores"

    if role == "supervisor":
        return user.area_name or "Assigned area"

    if role == "general_manager":
        return f"Store {user.store_number}" if user.store_number else "Assigned store"

    return f"{len(stores)} store(s)"


def manager_walk_percent(checklist: DailyChecklist):
    items = [
        item
        for item in checklist.items
        if item.section_name == "Manager's Walk"
    ]

    if not items:
        return None

    completed = sum(1 for item in items if item.is_completed)
    return round((completed / len(items)) * 100, 1)


def format_optional_number(value, suffix=""):
    if value is None:
        return "—"

    if isinstance(value, float):
        rendered = f"{value:,.2f}".rstrip("0").rstrip(".")
    else:
        rendered = str(value)

    return f"{rendered}{suffix}"


def collect_scope_data(
    user: User,
    stores: list[Store],
    brief_date,
):
    store_numbers = [store.store_number for store in stores]
    store_number_set = set(store_numbers)

    start_utc, end_utc = ops_timestamp_window_utc_naive(brief_date)

    checklists = (
        DailyChecklist.query
        .options(selectinload(DailyChecklist.items))
        .filter(
            DailyChecklist.store_number.in_(store_numbers),
            DailyChecklist.checklist_date == brief_date,
        )
        .order_by(DailyChecklist.store_number.asc())
        .all()
        if store_numbers
        else []
    )

    exceptions = (
        ChecklistException.query
        .filter(
            ChecklistException.store_number.in_(store_numbers),
            ChecklistException.checklist_date == brief_date,
        )
        .all()
        if store_numbers
        else []
    )

    nightly_reports = (
        NightlyNumbersReport.query
        .filter(
            NightlyNumbersReport.store_number.in_(store_numbers),
            NightlyNumbersReport.report_date == brief_date,
        )
        .order_by(NightlyNumbersReport.store_number.asc())
        .all()
        if store_numbers
        else []
    )

    svr_reports = (
        SVRReport.query
        .filter(
            SVRReport.store_number.in_(store_numbers),
            SVRReport.visit_date == brief_date,
        )
        .order_by(SVRReport.store_number.asc())
        .all()
        if store_numbers
        else []
    )

    maintenance_created = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(store_numbers),
            MaintenanceTicket.created_at >= start_utc,
            MaintenanceTicket.created_at < end_utc,
        )
        .order_by(
            MaintenanceTicket.priority.desc(),
            MaintenanceTicket.store_number.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    open_maintenance = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(store_numbers),
            ~MaintenanceTicket.status.in_(
                ["verified", "cancelled"]
            ),
        )
        .order_by(
            MaintenanceTicket.priority.desc(),
            MaintenanceTicket.created_at.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    dwps = (
        DWPRecord.query
        .filter(
            DWPRecord.store_number.in_(store_numbers),
            DWPRecord.created_at >= start_utc,
            DWPRecord.created_at < end_utc,
        )
        .order_by(
            DWPRecord.store_number.asc(),
            DWPRecord.created_at.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    hr_base = (
        HRDocumentRecipient.query
        .join(User, HRDocumentRecipient.user_id == User.id)
        .join(
            HRDocument,
            HRDocumentRecipient.document_id == HRDocument.id,
        )
        .filter(
            User.store_number.in_(store_numbers),
        )
    )

    hr_signed = (
        hr_base
        .filter(
            HRDocumentRecipient.acknowledged_at >= start_utc,
            HRDocumentRecipient.acknowledged_at < end_utc,
        )
        .order_by(
            User.store_number.asc(),
            HRDocumentRecipient.acknowledged_at.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    hr_pending = (
        hr_base
        .filter(
            HRDocument.is_active.is_(True),
            HRDocumentRecipient.status != "acknowledged",
        )
        .order_by(
            HRDocument.due_date.asc().nullslast(),
            User.store_number.asc(),
            HRDocumentRecipient.assigned_at.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    checklist_by_store = {
        row.store_number: row
        for row in checklists
    }

    exception_by_store = {
        row.store_number: row
        for row in exceptions
    }

    nightly_by_store = {
        row.store_number: row
        for row in nightly_reports
    }

    missing_checklists = sorted(
        store_number_set - set(checklist_by_store)
    )
    missing_nightly = sorted(
        store_number_set - set(nightly_by_store)
    )

    checklist_rows = []

    for store in stores:
        checklist = checklist_by_store.get(store.store_number)
        exception = exception_by_store.get(store.store_number)

        if checklist:
            checklist_rows.append({
                "store_number": store.store_number,
                "completion": round(
                    checklist.percent_complete or 0,
                    1,
                ),
                "integrity": round(
                    checklist.integrity_score or 0,
                    1,
                ),
                "manager_walk": manager_walk_percent(checklist),
                "status": checklist.status,
            })
        elif exception:
            checklist_rows.append({
                "store_number": store.store_number,
                "completion": round(
                    exception.percent_complete or 0,
                    1,
                ),
                "integrity": round(
                    exception.integrity_score or 0,
                    1,
                ),
                "manager_walk": (
                    0.0
                    if exception.manager_walk_missed
                    else None
                ),
                "status": "auto closed",
            })

    completed_checklists = sum(
        1
        for row in checklist_rows
        if row["completion"] >= 100
    )

    low_integrity = [
        row
        for row in checklist_rows
        if row["integrity"] < 70
    ]

    missed_walks = [
        row
        for row in checklist_rows
        if row["manager_walk"] is None
        or row["manager_walk"] < 100
    ]

    overdue_hr = [
        row
        for row in hr_pending
        if row.document
        and row.document.due_date
        and row.document.due_date < now_et().date()
    ]

    nightly_rows = []

    for report in nightly_reports:
        nightly_rows.append({
            "store_number": report.store_number,
            "sales": report.royalty_sales,
            "labor_variance": report.variable_labor,
            "food_variance": report.food_variance,
            "adt": report.adt,
            "load_time": report.load_time,
            "cash_diff": report.cash_diff,
        })

    return {
        "brief_date": brief_date,
        "stores": stores,
        "store_numbers": store_numbers,
        "checklist_rows": checklist_rows,
        "completed_checklists": completed_checklists,
        "missing_checklists": missing_checklists,
        "low_integrity": low_integrity,
        "missed_walks": missed_walks,
        "nightly_reports": nightly_reports,
        "nightly_rows": nightly_rows,
        "missing_nightly": missing_nightly,
        "svr_reports": svr_reports,
        "maintenance_created": maintenance_created,
        "open_maintenance": open_maintenance,
        "dwps": dwps,
        "hr_signed": hr_signed,
        "hr_pending": hr_pending,
        "hr_overdue": overdue_hr,
    }


def build_doughy_context(
    user: User,
    scope_label: str,
    data: dict,
):
    return {
        "module": "daily_bpi_ops_brief",
        "permission_filtered": True,
        "recipient": {
            "name": user.name,
            "role": user.role,
            "scope": scope_label,
        },
        "business_date": data["brief_date"].isoformat(),
        "summary": {
            "visible_stores": len(data["stores"]),
            "checklists_completed": data["completed_checklists"],
            "checklists_missing": data["missing_checklists"],
            "low_integrity_stores": [
                row["store_number"]
                for row in data["low_integrity"]
            ],
            "manager_walk_attention": [
                row["store_number"]
                for row in data["missed_walks"]
            ],
            "nightly_numbers_submitted": len(
                data["nightly_reports"]
            ),
            "nightly_numbers_missing": data["missing_nightly"],
            "svrs_completed": len(data["svr_reports"]),
            "maintenance_created": len(
                data["maintenance_created"]
            ),
            "open_maintenance": len(
                data["open_maintenance"]
            ),
            "dwps_submitted": len(data["dwps"]),
            "hr_documents_signed": len(data["hr_signed"]),
            "hr_documents_pending": len(data["hr_pending"]),
            "hr_documents_overdue": len(data["hr_overdue"]),
        },
        "checklists": data["checklist_rows"][:40],
        "nightly_numbers": data["nightly_rows"][:40],
        "dwps": [
            {
                "store_number": row.store_number,
                "team_member": row.team_member_name_snapshot,
                "type": row.discussion_type,
                "category": row.category,
                "submitted_by": row.submitted_by_name_snapshot,
                "acknowledged": bool(row.acknowledged_at),
            }
            for row in data["dwps"][:30]
        ],
        "hr_pending": [
            {
                "store_number": row.user.store_number,
                "employee": row.user.name,
                "document": row.document.title,
                "due_date": (
                    row.document.due_date.isoformat()
                    if row.document.due_date
                    else None
                ),
            }
            for row in data["hr_pending"][:40]
        ],
        "maintenance": [
            {
                "store_number": row.store_number,
                "title": row.title,
                "status": row.status,
                "priority": row.priority,
            }
            for row in data["open_maintenance"][:30]
        ],
    }


def fallback_doughy_take(data: dict) -> str:
    concerns = []

    if data["missing_checklists"]:
        concerns.append(
            f"{len(data['missing_checklists'])} store(s) had no checklist record"
        )

    if data["missed_walks"]:
        concerns.append(
            f"{len(data['missed_walks'])} store(s) need Manager's Walk review"
        )

    if data["missing_nightly"]:
        concerns.append(
            f"{len(data['missing_nightly'])} store(s) missed Nightly Numbers"
        )

    if data["hr_overdue"]:
        concerns.append(
            f"{len(data['hr_overdue'])} HR acknowledgment(s) are overdue"
        )

    if data["open_maintenance"]:
        concerns.append(
            f"{len(data['open_maintenance'])} maintenance ticket(s) remain open"
        )

    if not concerns:
        return (
            "Yesterday was clean across the visible scope. "
            "No major exception jumped out, so today's job is to "
            "protect the consistency and close any routine follow-up."
        )

    return (
        "Yesterday's main follow-up: "
        + "; ".join(concerns[:4])
        + ". Start with the oldest and highest-risk exceptions."
    )


def generate_doughy_take(
    user: User,
    scope_label: str,
    data: dict,
) -> str:
    prompt = (
        "Write Doughy's morning executive take for this BPI Ops daily email. "
        "Use only the supplied permission-filtered facts. "
        "Write 1 short paragraph, about 70 to 120 words. "
        "Call out what went well, the biggest risks, and the top priorities "
        "for today. Do not recommend discipline. Do not invent explanations. "
        "Use direct operations language."
    )

    try:
        answer = ask_doughy_ai(
            prompt,
            build_doughy_context(
                user=user,
                scope_label=scope_label,
                data=data,
            ),
        )

        if answer:
            return answer.strip()

    except Exception:
        pass

    return fallback_doughy_take(data)


def render_store_list(values):
    return ", ".join(values) if values else "None"


def render_email_body(
    user: User,
    scope_label: str,
    data: dict,
    doughy_take: str,
):
    date_label = data["brief_date"].strftime("%A, %B %d, %Y")

    checklist_lines = []

    for row in data["checklist_rows"]:
        walk = (
            f"{row['manager_walk']}%"
            if row["manager_walk"] is not None
            else "Not recorded"
        )

        checklist_lines.append(
            f"- Store {row['store_number']}: "
            f"{row['completion']}% complete | "
            f"Integrity {row['integrity']} | "
            f"Manager's Walk {walk}"
        )

    nightly_lines = []

    for report in data["nightly_reports"]:
        nightly_lines.append(
            f"- Store {report.store_number}: "
            f"Sales {format_optional_number(report.royalty_sales, '')} | "
            f"Variance to Ideal "
            f"{format_optional_number(report.variable_labor, '%')} | "
            f"Food {format_optional_number(report.food_variance, '%')} | "
            f"ADT {format_optional_number(report.adt)} | "
            f"Load {report.load_time or '—'} | "
            f"Cash {format_optional_number(report.cash_diff)}"
        )

    svr_lines = [
        f"- Store {row.store_number}: "
        f"{row.supervisor_name or 'Supervisor not listed'}"
        for row in data["svr_reports"]
    ]

    maintenance_lines = [
        f"- Store {row.store_number}: "
        f"[{(row.priority or 'normal').upper()}] "
        f"{row.title} — {row.status.replace('_', ' ').title()}"
        for row in data["open_maintenance"][:25]
    ]

    dwp_lines = [
        f"- Store {row.store_number}: "
        f"{row.team_member_name_snapshot} — "
        f"{row.discussion_type} / {row.category} — "
        f"submitted by {row.submitted_by_name_snapshot} — "
        f"{'Acknowledged' if row.acknowledged_at else 'Pending acknowledgment'}"
        for row in data["dwps"]
    ]

    signed_lines = [
        f"- Store {row.user.store_number or '—'}: "
        f"{row.user.name} signed “{row.document.title}”"
        for row in data["hr_signed"]
    ]

    pending_lines = []

    for row in data["hr_pending"][:40]:
        due_text = "No due date"

        if row.document.due_date:
            due_text = f"Due {row.document.due_date.strftime('%m/%d/%Y')}"

            if row.document.due_date < now_et().date():
                due_text += " — OVERDUE"

        pending_lines.append(
            f"- Store {row.user.store_number or '—'}: "
            f"{row.user.name} — {row.document.title} — {due_text}"
        )

    return (
        f"Good morning {user.name},\n\n"
        f"DOUGHY'S TAKE\n"
        f"{doughy_take}\n\n"
        f"REPORT DATE\n"
        f"{date_label}\n"
        f"Scope: {scope_label}\n"
        f"Visible stores: {len(data['stores'])}\n\n"
        f"CHECKLIST EXECUTION\n"
        f"Completed at 100%: "
        f"{data['completed_checklists']}/{len(data['stores'])}\n"
        f"No checklist record: "
        f"{render_store_list(data['missing_checklists'])}\n"
        f"Integrity below 70: "
        f"{render_store_list([row['store_number'] for row in data['low_integrity']])}\n"
        f"Manager's Walk needs review: "
        f"{render_store_list([row['store_number'] for row in data['missed_walks']])}\n\n"
        f"{chr(10).join(checklist_lines) if checklist_lines else '- No checklist activity'}\n\n"
        f"NIGHTLY NUMBERS\n"
        f"Submitted: {len(data['nightly_reports'])}/{len(data['stores'])}\n"
        f"Missing: {render_store_list(data['missing_nightly'])}\n\n"
        f"{chr(10).join(nightly_lines) if nightly_lines else '- No Nightly Numbers submitted'}\n\n"
        f"SVRs COMPLETED\n"
        f"{chr(10).join(svr_lines) if svr_lines else '- None'}\n\n"
        f"MAINTENANCE\n"
        f"Created yesterday: {len(data['maintenance_created'])}\n"
        f"Currently open/in progress: {len(data['open_maintenance'])}\n"
        f"{chr(10).join(maintenance_lines) if maintenance_lines else '- No open maintenance tickets'}\n\n"
        f"DWPs SUBMITTED\n"
        f"{chr(10).join(dwp_lines) if dwp_lines else '- None'}\n\n"
        f"HR DOCUMENTS SIGNED\n"
        f"{chr(10).join(signed_lines) if signed_lines else '- None'}\n\n"
        f"HR DOCUMENTS PENDING\n"
        f"Pending: {len(data['hr_pending'])}\n"
        f"Overdue: {len(data['hr_overdue'])}\n"
        f"{chr(10).join(pending_lines) if pending_lines else '- None'}\n\n"
        f"- Doughy\n"
        f"BPI Ops"
    )


def eligible_recipients():
    return (
        User.query
        .filter(
            User.role.in_(RECIPIENT_ROLES),
            User.is_active.is_(True),
            User.email_enabled.is_(True),
        )
        .order_by(User.role.asc(), User.name.asc())
        .all()
    )


def reserve_log(
    brief_date,
    user: User,
    email: str,
    scope_label: str,
    force: bool,
):
    existing = DoughyDailyBriefLog.query.filter_by(
        brief_date=brief_date,
        recipient_user_id=user.id,
    ).first()

    if existing and not force:
        return None, "already_sent"

    if existing:
        existing.recipient_email = email
        existing.recipient_role = user.role
        existing.scope_label = scope_label
        existing.status = "pending"
        existing.error_message = None
        existing.sent_at = None
        db.session.commit()
        return existing, None

    log = DoughyDailyBriefLog(
        brief_date=brief_date,
        recipient_user_id=user.id,
        recipient_email=email,
        recipient_role=user.role,
        scope_label=scope_label,
        status="pending",
    )

    db.session.add(log)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "already_sent"

    return log, None


def send_daily_briefs(
    *,
    force: bool = False,
    test_email: str | None = None,
):
    brief_date = completed_ops_date()

    results = {
        "ok": True,
        "brief_date": brief_date.isoformat(),
        "sent": [],
        "skipped": [],
        "failed": [],
    }

    for user in eligible_recipients():
        configured_email = user.get_notification_email()

        if not configured_email:
            results["skipped"].append({
                "user_id": user.id,
                "name": user.name,
                "reason": "no_notification_email",
            })
            continue

        stores = visible_stores_for_user(user)

        if not stores:
            results["skipped"].append({
                "user_id": user.id,
                "name": user.name,
                "reason": "no_visible_stores",
            })
            continue

        scope_label = recipient_scope_label(user, stores)

        log, skip_reason = reserve_log(
            brief_date=brief_date,
            user=user,
            email=configured_email,
            scope_label=scope_label,
            force=force,
        )

        if skip_reason:
            results["skipped"].append({
                "user_id": user.id,
                "name": user.name,
                "reason": skip_reason,
            })
            continue

        delivery_email = test_email or configured_email

        try:
            data = collect_scope_data(
                user=user,
                stores=stores,
                brief_date=brief_date,
            )

            doughy_take = generate_doughy_take(
                user=user,
                scope_label=scope_label,
                data=data,
            )

            body = render_email_body(
                user=user,
                scope_label=scope_label,
                data=data,
                doughy_take=doughy_take,
            )

            subject_prefix = "[TEST] " if test_email else ""

            send_email(
                to_email=delivery_email,
                subject=(
                    f"{subject_prefix}Doughy's BPI Ops Daily Brief — "
                    f"{brief_date.strftime('%b %d, %Y')}"
                ),
                body=body,
            )

            log.status = "sent"
            log.sent_at = datetime.utcnow()
            log.error_message = None
            db.session.commit()

            results["sent"].append({
                "user_id": user.id,
                "name": user.name,
                "role": user.role,
                "configured_email": configured_email,
                "delivered_to": delivery_email,
                "scope": scope_label,
            })

        except Exception as exc:
            db.session.rollback()

            failed_log = DoughyDailyBriefLog.query.get(log.id)

            if failed_log:
                failed_log.status = "failed"
                failed_log.error_message = str(exc)[:2000]
                db.session.commit()

            results["failed"].append({
                "user_id": user.id,
                "name": user.name,
                "error": str(exc),
            })

    results["sent_count"] = len(results["sent"])
    results["skipped_count"] = len(results["skipped"])
    results["failed_count"] = len(results["failed"])

    return results
