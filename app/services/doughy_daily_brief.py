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


def checklist_section_percent(
    checklist: DailyChecklist,
    section_name: str,
):
    items = [
        item
        for item in checklist.items
        if item.section_name == section_name
    ]

    if not items:
        return None

    completed = sum(
        1
        for item in items
        if item.is_completed
    )

    return round(
        (completed / len(items)) * 100,
        1,
    )


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

    completed_maintenance = [
        row
        for row in maintenance_created
        if (row.status or "").strip().lower()
        in {"complete", "completed", "verified"}
    ]

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
                "opening": checklist_section_percent(
                    checklist,
                    "Before Open / Before 10:30",
                ),
                "restock": checklist_section_percent(
                    checklist,
                    "3-O'Clock Restock",
                ),
                "manager_walk": checklist_section_percent(
                    checklist,
                    "Manager's Walk",
                ),
                "integrity": round(
                    checklist.integrity_score or 0,
                    1,
                ),
                "status": checklist.status,
            })
        elif exception:
            checklist_rows.append({
                "store_number": store.store_number,
                "opening": None,
                "restock": None,
                "manager_walk": (
                    0.0
                    if exception.manager_walk_missed
                    else None
                ),
                "integrity": round(
                    exception.integrity_score or 0,
                    1,
                ),
                "status": "auto closed",
            })

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
        "missing_checklists": missing_checklists,
        "low_integrity": low_integrity,
        "missed_walks": missed_walks,
        "nightly_reports": nightly_reports,
        "nightly_rows": nightly_rows,
        "missing_nightly": missing_nightly,
        "svr_reports": svr_reports,
        "completed_maintenance": completed_maintenance,
        "dwps": dwps,
        "hr_signed": hr_signed,
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
            "maintenance_completed": len(
                data["completed_maintenance"]
            ),
            "dwps_submitted": len(data["dwps"]),
            "hr_documents_signed": len(data["hr_signed"]),
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
        "maintenance": [
            {
                "store_number": row.store_number,
                "title": row.title,
                "status": row.status,
                "priority": row.priority,
            }
            for row in data["completed_maintenance"][:30]
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
    date_label = data["brief_date"].strftime(
        "%A, %B %d, %Y"
    )

    if user.role == "hr":
        dwp_lines = [
            (
                f"- Store {row.store_number} | "
                f"{row.team_member_name_snapshot} | "
                f"{row.discussion_type} / {row.category} | "
                f"Submitted by {row.submitted_by_name_snapshot}"
            )
            for row in data["dwps"]
        ]

        signed_lines = [
            (
                f"- Store {row.user.store_number or '—'} | "
                f"{row.user.name} signed "
                f"“{row.document.title}”"
            )
            for row in data["hr_signed"]
        ]

        return (
            f"Good morning {user.name},\n\n"
            f"HR DAILY BRIEF\n"
            f"{date_label}\n"
            f"Scope: {scope_label}\n\n"

            f"DWPs SUBMITTED\n"
            f"{chr(10).join(dwp_lines) if dwp_lines else '- None'}\n\n"

            f"HR DOCUMENTS SIGNED\n"
            f"{chr(10).join(signed_lines) if signed_lines else '- None'}\n\n"

            f"- Doughy\n"
            f"BPI Ops"
        )

    def pct(value):
        return (
            f"{value:.0f}%"
            if value is not None
            else "Not recorded"
        )

    checklist_lines = [
        (
            f"- Store {row['store_number']} | "
            f"Open {pct(row['opening'])} | "
            f"3 PM {pct(row['restock'])} | "
            f"Manager's Walk {pct(row['manager_walk'])} | "
            f"Integrity {row['integrity']:.1f}"
        )
        for row in data["checklist_rows"]
    ]

    nightly_lines = [
        (
            f"- Store {report.store_number} | "
            f"Sales {format_optional_number(report.royalty_sales)} | "
            f"Variance to Ideal "
            f"{format_optional_number(report.variable_labor, '%')} | "
            f"Food {format_optional_number(report.food_variance, '%')} | "
            f"ADT {format_optional_number(report.adt)} | "
            f"Load {report.load_time or '—'} | "
            f"Cash {format_optional_number(report.cash_diff)}"
        )
        for report in data["nightly_reports"]
    ]

    svr_lines = [
        (
            f"- Store {row.store_number} | "
            f"{row.supervisor_name or 'Supervisor not listed'}"
        )
        for row in data["svr_reports"]
    ]

    maintenance_lines = [
        (
            f"- Store {row.store_number} | "
            f"{row.title}"
        )
        for row in data["completed_maintenance"]
    ]

    dwp_lines = [
        (
            f"- Store {row.store_number} | "
            f"{row.team_member_name_snapshot} | "
            f"{row.discussion_type} / {row.category} | "
            f"Submitted by {row.submitted_by_name_snapshot}"
        )
        for row in data["dwps"]
    ]

    signed_lines = [
        (
            f"- Store {row.user.store_number or '—'} | "
            f"{row.user.name} signed "
            f"“{row.document.title}”"
        )
        for row in data["hr_signed"]
    ]

    return (
        f"Good morning {user.name},\n\n"
        f"DOUGHY'S TAKE\n"
        f"{doughy_take}\n\n"

        f"YESTERDAY AT A GLANCE\n"
        f"{date_label}\n"
        f"Scope: {scope_label}\n"
        f"Stores: {len(data['stores'])}\n\n"

        f"CHECKLIST EXECUTION\n"
        f"Open = Before Open / Before 10:30\n"
        f"Missing checklist records: "
        f"{render_store_list(data['missing_checklists'])}\n\n"
        f"{chr(10).join(checklist_lines) if checklist_lines else '- No checklist records'}\n\n"

        f"NIGHTLY NUMBERS\n"
        f"Submitted: "
        f"{len(data['nightly_reports'])}/{len(data['stores'])}\n"
        f"Missing: "
        f"{render_store_list(data['missing_nightly'])}\n"
        f"{chr(10).join(nightly_lines) if nightly_lines else '- None submitted'}\n\n"

        f"SVRs COMPLETED\n"
        f"{chr(10).join(svr_lines) if svr_lines else '- None'}\n\n"

        f"MAINTENANCE COMPLETED\n"
        f"{chr(10).join(maintenance_lines) if maintenance_lines else '- None'}\n\n"

        f"DWPs SUBMITTED\n"
        f"{chr(10).join(dwp_lines) if dwp_lines else '- None'}\n\n"

        f"HR DOCUMENTS SIGNED\n"
        f"{chr(10).join(signed_lines) if signed_lines else '- None'}\n\n"

        f"- Doughy\n"
        f"BPI Ops"
    )

def eligible_recipients():
    users = (
        User.query
        .filter(
            User.role.in_(list(RECIPIENT_ROLES)),
            User.is_active.is_(True),
            User.email_enabled.is_(True),
        )
        .order_by(User.id.desc())
        .all()
    )

    role_priority = {
        "admin": 0,
        "hr": 1,
        "supervisor": 2,
        "general_manager": 3,
    }

    selected_by_email = {}

    for user in users:
        email = (
            user.get_notification_email() or ""
        ).strip().lower()

        if not email:
            continue

        existing = selected_by_email.get(email)

        if existing is None:
            selected_by_email[email] = user
            continue

        user_priority = role_priority.get(
            (user.role or "").strip().lower(),
            99,
        )
        existing_priority = role_priority.get(
            (existing.role or "").strip().lower(),
            99,
        )

        if user_priority < existing_priority:
            selected_by_email[email] = user
            continue

        if (
            user_priority == existing_priority
            and user.id > existing.id
        ):
            selected_by_email[email] = user

    return sorted(
        selected_by_email.values(),
        key=lambda user: (
            role_priority.get(
                (user.role or "").strip().lower(),
                99,
            ),
            (user.name or "").lower(),
        ),
    )


def reserve_log(
    brief_date,
    user: User,
    email: str,
    scope_label: str,
    force: bool,
):
    normalized_email = (email or "").strip().lower()

    existing = (
        DoughyDailyBriefLog.query
        .filter(
            DoughyDailyBriefLog.brief_date == brief_date,
            db.func.lower(
                DoughyDailyBriefLog.recipient_email
            ) == normalized_email,
        )
        .order_by(DoughyDailyBriefLog.id.desc())
        .first()
    )

    if (
        existing
        and existing.status == "sent"
        and not force
    ):
        return None, "already_sent"

    if existing:
        existing.recipient_user_id = user.id
        existing.recipient_email = normalized_email
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
        recipient_email=normalized_email,
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
