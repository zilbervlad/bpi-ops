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
    "maintenance",
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

    if role in {"admin", "hr", "maintenance"}:
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

    if role in {"admin", "hr", "maintenance"}:
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

    today_et = now_et().date()

    maintenance_scheduled_today = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(store_numbers),
            MaintenanceTicket.scheduled_date == today_et,
            MaintenanceTicket.status != "complete",
        )
        .order_by(
            MaintenanceTicket.scheduled_time.asc().nullslast(),
            MaintenanceTicket.priority.desc(),
            MaintenanceTicket.store_number.asc(),
            MaintenanceTicket.id.asc(),
        )
        .all()
        if store_numbers
        else []
    )

    maintenance_completed_yesterday = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(store_numbers),
            MaintenanceTicket.scheduled_date == brief_date,
            MaintenanceTicket.status == "complete",
        )
        .order_by(
            MaintenanceTicket.scheduled_time.asc().nullslast(),
            MaintenanceTicket.store_number.asc(),
            MaintenanceTicket.id.asc(),
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
        "maintenance_scheduled_today": maintenance_scheduled_today,
        "maintenance_completed_yesterday": maintenance_completed_yesterday,
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
    rows = data["checklist_rows"]

    def value(row, key):
        current = row.get(key)
        return current if current is not None else 0

    def severity(row):
        opening = value(row, "opening")
        restock = value(row, "restock")
        walk = value(row, "manager_walk")
        integrity = value(row, "integrity")

        if (
            integrity < 50
            or opening < 75
            or restock < 50
            or walk < 50
        ):
            return 2

        if (
            integrity < 70
            or opening < 90
            or restock < 90
            or walk < 90
        ):
            return 1

        return 0

    priority = sorted(
        [row for row in rows if severity(row) == 2],
        key=lambda row: (
            value(row, "integrity"),
            value(row, "manager_walk"),
            value(row, "restock"),
            value(row, "opening"),
        ),
    )

    watch = sorted(
        [row for row in rows if severity(row) == 1],
        key=lambda row: (
            value(row, "integrity"),
            value(row, "manager_walk"),
        ),
    )

    strong = sorted(
        [row for row in rows if severity(row) == 0],
        key=lambda row: row["store_number"],
    )

    sentences = []

    if strong:
        strong_stores = ", ".join(
            row["store_number"]
            for row in strong[:4]
        )

        sentences.append(
            f"{strong_stores} delivered the strongest checklist "
            f"execution yesterday."
        )

    if priority:
        top = priority[0]

        problems = []

        if value(top, "opening") < 90:
            problems.append(
                f"Open finished at {value(top, 'opening'):.0f}%"
            )

        if value(top, "restock") < 90:
            problems.append(
                f"3 PM Restock finished at "
                f"{value(top, 'restock'):.0f}%"
            )

        if value(top, "manager_walk") < 90:
            problems.append(
                f"Manager's Walk finished at "
                f"{value(top, 'manager_walk'):.0f}%"
            )

        if value(top, "integrity") < 70:
            problems.append(
                f"integrity was {value(top, 'integrity'):.1f}"
            )

        sentences.append(
            f"Today’s first follow-up should be store "
            f"{top['store_number']}: "
            + ", ".join(problems)
            + "."
        )

        remaining_priority = [
            row["store_number"]
            for row in priority[1:4]
        ]

        if remaining_priority:
            sentences.append(
                "Additional priority stores are "
                + ", ".join(remaining_priority)
                + "."
            )

    elif watch:
        watch_stores = ", ".join(
            row["store_number"]
            for row in watch[:4]
        )

        sentences.append(
            f"The main checklist follow-up is with "
            f"{watch_stores}; these stores were close, but still had "
            f"section or integrity gaps."
        )

    if data["missing_nightly"]:
        sentences.append(
            "Nightly Numbers are missing from "
            + ", ".join(data["missing_nightly"])
            + "."
        )

    if not sentences:
        return (
            "Yesterday’s reporting was complete across the visible "
            "stores, with no major checklist or Nightly Numbers "
            "exceptions requiring follow-up."
        )

    return " ".join(sentences)

def generate_doughy_take(
    user: User,
    scope_label: str,
    data: dict,
) -> str:
    operations_context = {
        "module": "bpi_ops_daily_operations_summary",
        "request_type": "executive_operations_summary",
        "permission_filtered": True,
        "recipient": {
            "name": user.name,
            "role": user.role,
            "scope": scope_label,
        },
        "business_date": data["brief_date"].isoformat(),
        "stores": [
            {
                "store_number": row["store_number"],
                "opening_percent": row["opening"],
                "three_pm_restock_percent": row["restock"],
                "manager_walk_percent": row["manager_walk"],
                "integrity_score": row["integrity"],
            }
            for row in data["checklist_rows"]
        ],
        "missing_checklist_records": data["missing_checklists"],
        "missing_nightly_numbers": data["missing_nightly"],
        "nightly_numbers": data["nightly_rows"],
        "svrs_completed": [
            {
                "store_number": row.store_number,
                "supervisor": row.supervisor_name,
            }
            for row in data["svr_reports"]
        ],
    }

    recipient_instruction = (
        "This recipient manages one store. Focus entirely on that store. "
        "Explain exactly what happened yesterday and give a short, ordered "
        "action plan for today. Do not compare it with other stores. "
        if (user.role or "").strip().lower() == "general_manager"
        else
        "This recipient oversees multiple stores. Compare execution across "
        "their visible stores and prioritize the most important follow-up. "
    )

    prompt = (
        recipient_instruction
        + "This is an executive BPI operations summary, not a DWP, HR, "
        "maintenance, or database lookup request. "
        "Write Doughy's morning operations take using only the supplied "
        "permission-filtered operations facts. "
        "Write one concise paragraph of 90 to 140 words. "
        "Name the strongest stores and the three stores requiring the most "
        "urgent follow-up. Cite the exact Open, 3 PM Restock, Manager's Walk, "
        "integrity, or missing Nightly Numbers facts that justify the priority. "
        "Distinguish minor misses from serious failures. "
        "End with a practical order of follow-up for today. "
        "Do not discuss DWPs or HR documents. "
        "Do not answer with 'no records were found.' "
        "Do not invent causes."
    )

    try:
        answer = ask_doughy_ai(
            prompt,
            operations_context,
        )

        if answer:
            cleaned = answer.strip()

            rejected_phrases = (
                "no dwp records",
                "no records were found",
                "requested period",
            )

            if not any(
                phrase in cleaned.lower()
                for phrase in rejected_phrases
            ):
                return cleaned

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

    if user.role == "maintenance":
        def maintenance_time(ticket):
            if not ticket.scheduled_time:
                return "Time not set"

            hour = ticket.scheduled_time.hour
            minute = ticket.scheduled_time.minute
            suffix = "AM" if hour < 12 else "PM"
            display_hour = hour % 12 or 12

            return f"{display_hour}:{minute:02d} {suffix}"

        scheduled_lines = [
            (
                f"- Store {ticket.store_number} | "
                f"{maintenance_time(ticket)} | "
                f"{ticket.title} | "
                f"{(ticket.priority or 'normal').upper()}"
            )
            for ticket in data["maintenance_scheduled_today"]
        ]

        completed_lines = [
            (
                f"- Store {ticket.store_number} | "
                f"{ticket.title}"
            )
            for ticket in data["maintenance_completed_yesterday"]
        ]

        return (
            f"Good morning {user.name},\n\n"
            f"MAINTENANCE DAILY BRIEF\n"
            f"{date_label}\n"
            f"Scope: {scope_label}\n\n"

            f"SCHEDULED FOR TODAY\n"
            f"{chr(10).join(scheduled_lines) if scheduled_lines else '- None'}\n\n"

            f"COMPLETED YESTERDAY\n"
            f"{chr(10).join(completed_lines) if completed_lines else '- None'}\n\n"

            f"- Doughy\n"
            f"BPI Ops"
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

    if user.role == "general_manager":
        row = (
            data["checklist_rows"][0]
            if data["checklist_rows"]
            else None
        )

        store_number = (
            str(user.store_number)
            if user.store_number
            else (
                data["store_numbers"][0]
                if data["store_numbers"]
                else "Assigned store"
            )
        )

        def store_pct(value):
            return (
                f"{value:.0f}%"
                if value is not None
                else "Not recorded"
            )

        priorities = []

        if not row:
            priorities.append(
                "Complete and submit today's checklist."
            )
        else:
            if (
                row["opening"] is None
                or row["opening"] < 100
            ):
                priorities.append(
                    "Close the remaining opening checklist gap."
                )

            if (
                row["restock"] is None
                or row["restock"] < 100
            ):
                priorities.append(
                    "Complete the full 3 PM Restock."
                )

            if (
                row["manager_walk"] is None
                or row["manager_walk"] < 100
            ):
                priorities.append(
                    "Complete the full Manager's Walk."
                )

            if row["integrity"] < 70:
                priorities.append(
                    "Review checklist timing and integrity."
                )

        nightly_report = (
            data["nightly_reports"][0]
            if data["nightly_reports"]
            else None
        )

        if not nightly_report:
            priorities.append(
                "Submit Nightly Numbers."
            )

        if not priorities:
            priorities.append(
                "Repeat yesterday's strong execution."
            )

        priority_lines = [
            f"{index}. {priority}"
            for index, priority in enumerate(
                priorities,
                start=1,
            )
        ]

        if row:
            checklist_text = (
                f"Open: {store_pct(row['opening'])}\n"
                f"3 PM Restock: {store_pct(row['restock'])}\n"
                f"Manager's Walk: "
                f"{store_pct(row['manager_walk'])}\n"
                f"Integrity: {row['integrity']:.1f}"
            )
        else:
            checklist_text = "No checklist record found."

        if nightly_report:
            nightly_text = (
                f"Sales: "
                f"{format_optional_number(nightly_report.royalty_sales)}\n"
                f"Labor: "
                f"{format_optional_number(nightly_report.variable_labor, '%')}\n"
                f"Food: "
                f"{format_optional_number(nightly_report.food_variance, '%')}\n"
                f"ADT: "
                f"{format_optional_number(nightly_report.adt)}\n"
                f"Load: {nightly_report.load_time or '—'}\n"
                f"Cash: "
                f"{format_optional_number(nightly_report.cash_diff)}"
            )
        else:
            nightly_text = "Not submitted."

        svr_lines = [
            (
                f"- Completed by "
                f"{report.supervisor_name or 'Supervisor'}"
            )
            for report in data["svr_reports"]
        ]

        maintenance_lines = [
            f"- {ticket.title}"
            for ticket in data["completed_maintenance"]
        ]

        activity_sections = []

        if svr_lines:
            activity_sections.append(
                "SVR\n" + "\n".join(svr_lines)
            )

        if maintenance_lines:
            activity_sections.append(
                "MAINTENANCE COMPLETED\n"
                + "\n".join(maintenance_lines)
            )

        activity_text = (
            "\n\n".join(activity_sections)
            if activity_sections
            else "No additional activity recorded."
        )

        return (
            f"Good morning {user.name},\n\n"

            f"STORE {store_number} DAILY BRIEF\n"
            f"{date_label}\n\n"

            f"DOUGHY'S TAKE\n"
            f"{doughy_take}\n\n"

            f"YESTERDAY'S CHECKLIST\n"
            f"{checklist_text}\n\n"

            f"NIGHTLY NUMBERS\n"
            f"{nightly_text}\n\n"

            f"TODAY'S PRIORITIES\n"
            f"{chr(10).join(priority_lines)}\n\n"

            f"OTHER STORE ACTIVITY\n"
            f"{activity_text}\n\n"

            f"- Doughy\n"
            f"BPI Ops"
        )

    if (user.role or "").strip().lower() == "general_manager":
        checklist_row = (
            data["checklist_rows"][0]
            if data["checklist_rows"]
            else None
        )

        nightly_report = (
            data["nightly_reports"][0]
            if data["nightly_reports"]
            else None
        )

        store_number = (
            str(user.store_number)
            if user.store_number
            else (
                data["store_numbers"][0]
                if data["store_numbers"]
                else "Assigned Store"
            )
        )

        def store_pct(value):
            return (
                f"{value:.0f}%"
                if value is not None
                else "Not recorded"
            )

        if checklist_row:
            checklist_text = (
                f"Open: {store_pct(checklist_row['opening'])}\n"
                f"3 PM Restock: "
                f"{store_pct(checklist_row['restock'])}\n"
                f"Manager's Walk: "
                f"{store_pct(checklist_row['manager_walk'])}\n"
                f"Integrity: {checklist_row['integrity']:.1f}"
            )
        else:
            checklist_text = "No checklist record was found."

        if nightly_report:
            nightly_text = (
                f"Sales: "
                f"{format_optional_number(nightly_report.royalty_sales)}\n"
                f"Variance to Ideal: "
                f"{format_optional_number(nightly_report.variable_labor, '%')}\n"
                f"Food: "
                f"{format_optional_number(nightly_report.food_variance, '%')}\n"
                f"ADT: "
                f"{format_optional_number(nightly_report.adt)}\n"
                f"Load: {nightly_report.load_time or '—'}\n"
                f"Cash: "
                f"{format_optional_number(nightly_report.cash_diff)}"
            )
        else:
            nightly_text = "Not submitted."

        priorities = []

        if not checklist_row:
            priorities.append(
                "Complete and submit the full daily checklist."
            )
        else:
            if (
                checklist_row["manager_walk"] is None
                or checklist_row["manager_walk"] < 100
            ):
                priorities.append(
                    "Complete the full Manager's Walk."
                )

            if not nightly_report:
                priorities.append(
                    "Submit Nightly Numbers."
                )

            if (
                checklist_row["restock"] is None
                or checklist_row["restock"] < 100
            ):
                priorities.append(
                    "Complete the full 3 PM Restock."
                )

            if (
                checklist_row["opening"] is None
                or checklist_row["opening"] < 100
            ):
                priorities.append(
                    "Close the remaining opening checklist gap."
                )

            if checklist_row["integrity"] < 70:
                priorities.append(
                    "Review checklist timing and improve integrity."
                )

        if not checklist_row and not nightly_report:
            priorities.append(
                "Submit Nightly Numbers."
            )

        if not priorities:
            priorities.append(
                "Repeat yesterday's strong execution."
            )

        priority_lines = [
            f"{index}. {priority}"
            for index, priority in enumerate(
                priorities,
                start=1,
            )
        ]

        svr_lines = [
            (
                f"- Completed by "
                f"{report.supervisor_name or 'Supervisor'}"
            )
            for report in data["svr_reports"]
        ]

        maintenance_lines = [
            f"- {ticket.title}"
            for ticket in data["completed_maintenance"]
        ]

        activity_sections = []

        if svr_lines:
            activity_sections.append(
                "SVR COMPLETED\n"
                + "\n".join(svr_lines)
            )

        if maintenance_lines:
            activity_sections.append(
                "MAINTENANCE COMPLETED\n"
                + "\n".join(maintenance_lines)
            )

        activity_text = (
            "\n\n".join(activity_sections)
            if activity_sections
            else "No additional store activity recorded."
        )

        return (
            f"Good morning {user.name},\n\n"

            f"STORE {store_number} MORNING BRIEF\n"
            f"{date_label}\n\n"

            f"DOUGHY'S TAKE\n"
            f"{doughy_take}\n\n"

            f"YESTERDAY'S CHECKLIST\n"
            f"{checklist_text}\n\n"

            f"NIGHTLY NUMBERS\n"
            f"{nightly_text}\n\n"

            f"TODAY'S PRIORITIES\n"
            f"{chr(10).join(priority_lines)}\n\n"

            f"OTHER STORE ACTIVITY\n"
            f"{activity_text}\n\n"

            f"- Doughy\n"
            f"BPI Ops"
        )

    def pct(value):
        return (
            f"{value:.0f}%"
            if value is not None
            else "Not recorded"
        )

    def severity(row):
        opening = row["opening"] if row["opening"] is not None else 0
        restock = row["restock"] if row["restock"] is not None else 0
        walk = (
            row["manager_walk"]
            if row["manager_walk"] is not None
            else 0
        )
        integrity = row["integrity"]

        if (
            integrity < 50
            or opening < 75
            or restock < 50
            or walk < 50
        ):
            return "PRIORITY"

        if (
            integrity < 70
            or opening < 90
            or restock < 90
            or walk < 90
        ):
            return "WATCH"

        return "STRONG"

    priority_rows = sorted(
        [
            row
            for row in data["checklist_rows"]
            if severity(row) == "PRIORITY"
        ],
        key=lambda row: (
            row["integrity"],
            row["manager_walk"]
            if row["manager_walk"] is not None
            else -1,
            row["restock"]
            if row["restock"] is not None
            else -1,
        ),
    )

    watch_rows = sorted(
        [
            row
            for row in data["checklist_rows"]
            if severity(row) == "WATCH"
        ],
        key=lambda row: (
            row["integrity"],
            row["store_number"],
        ),
    )

    strong_rows = sorted(
        [
            row
            for row in data["checklist_rows"]
            if severity(row) == "STRONG"
        ],
        key=lambda row: row["store_number"],
    )

    def checklist_block(row):
        return (
            f"{row['store_number']}\n"
            f"  Open: {pct(row['opening'])}\n"
            f"  During Dayshift: {pct(row.get('dayshift'))}\n"
            f"  3 PM Restock: {pct(row['restock'])}\n"
            f"  Manager's Walk: {pct(row['manager_walk'])}\n"
            f"  Integrity: {row['integrity']:.1f}"
        )

    detailed_priority_rows = priority_rows[:6]
    remaining_priority_rows = priority_rows[6:]

    priority_text = "\n\n".join(
        checklist_block(row)
        for row in detailed_priority_rows
    )

    if remaining_priority_rows:
        priority_text += (
            "\n\nAdditional priority stores: "
            + ", ".join(
                row["store_number"]
                for row in remaining_priority_rows
            )
        )

    watch_text = "\n\n".join(
        checklist_block(row)
        for row in watch_rows
    )

    strong_text = "\n".join(
        (
            f"- {row['store_number']}: "
            f"Open {pct(row['opening'])}, "
            f"Dayshift {pct(row.get('dayshift'))}, "
            f"3 PM {pct(row['restock'])}, "
            f"Walk {pct(row['manager_walk'])}, "
            f"Integrity {row['integrity']:.1f}"
        )
        for row in strong_rows
    )

    nightly_exception_lines = []

    for report in data["nightly_reports"]:
        issues = []

        if report.adt is not None and report.adt > 25:
            issues.append(
                f"ADT {format_optional_number(report.adt)}"
            )

        load_value = report.load_time

        try:
            normalized_load = float(load_value)
        except (TypeError, ValueError):
            normalized_load = None

        if normalized_load is not None and normalized_load > 3.5:
            issues.append(f"Load {load_value}")

        if (
            report.food_variance is not None
            and abs(report.food_variance) > 0.5
        ):
            issues.append(
                f"Food "
                f"{format_optional_number(report.food_variance, '%')}"
            )

        if (
            report.cash_diff is not None
            and abs(report.cash_diff) > 5
        ):
            issues.append(
                f"Cash {format_optional_number(report.cash_diff)}"
            )

        if issues:
            nightly_exception_lines.append(
                f"- Store {report.store_number}: "
                + " · ".join(issues)
            )


    activity_lines = []

    if data["svr_reports"]:
        activity_lines.append(
            f"SVRs completed yesterday: {len(data['svr_reports'])}"
        )

        activity_lines.extend(
            (
                f"  - Store {row.store_number}: "
                f"{row.supervisor_name or 'Supervisor not listed'}"
            )
            for row in data["svr_reports"]
        )

    maintenance_completed_yesterday = (
        data.get("maintenance_completed_yesterday") or []
    )

    if maintenance_completed_yesterday:
        activity_lines.append(
            "Maintenance completed yesterday: "
            f"{len(maintenance_completed_yesterday)}"
        )

        activity_lines.extend(
            (
                f"  - Store {row.store_number}: "
                f"{row.title}"
            )
            for row in maintenance_completed_yesterday
        )

    if data["dwps"]:
        activity_lines.append(
            f"DWPs submitted yesterday: {len(data['dwps'])}"
        )

        activity_lines.extend(
            (
                f"  - Store {row.store_number}: "
                f"{row.team_member_name_snapshot or 'Team member not listed'} | "
                f"{row.discussion_type or 'Type not listed'}"
                f"{' / ' + row.category if row.category else ''} | "
                f"Submitted by "
                f"{row.submitted_by_name_snapshot or 'Submitter not listed'}"
            )
            for row in data["dwps"]
        )

    if data["hr_signed"]:
        activity_lines.append(
            f"HR documents signed yesterday: "
            f"{len(data['hr_signed'])}"
        )

    data_quality_lines = []

    for report in data["nightly_reports"]:
        if report.adt is not None and report.adt >= 120:
            data_quality_lines.append(
                f"- Store {report.store_number}: "
                f"ADT entered as "
                f"{format_optional_number(report.adt)}; "
                f"confirm this is not a data-entry error."
            )

    return (
        f"Good morning {user.name},\n\n"

        f"DOUGHY'S MORNING BRIEF\n"
        f"{date_label}\n"
        f"Scope: {scope_label}\n\n"

        f"DOUGHY'S TAKE\n"
        f"{doughy_take}\n\n"

        f"EXECUTIVE SNAPSHOT\n"
        f"Priority stores: {len(priority_rows)}\n"
        f"Watch stores: {len(watch_rows)}\n"
        f"Strong stores: {len(strong_rows)}\n"
        f"Nightly Numbers: "
        f"{len(data['nightly_reports'])}/{len(data['stores'])} submitted\n"
        f"Missing Nightly Numbers: "
        f"{render_store_list(data['missing_nightly'])}\n\n"

        f"PRIORITY FOLLOW-UP\n"
        f"{priority_text if priority_text else '- None'}\n\n"

        f"WATCH LIST\n"
        f"{watch_text if watch_text else '- None'}\n\n"

        f"STRONG EXECUTION\n"
        f"{strong_text if strong_text else '- None'}\n\n"

        f"NIGHTLY NUMBERS EXCEPTIONS\n"
        f"Missing submissions: "
        f"{render_store_list(data['missing_nightly'])}\n"
        f"{chr(10).join(nightly_exception_lines) if nightly_exception_lines else '- No submitted-store exceptions'}\n\n"

        f"DATA QUALITY REVIEW\n"
        f"{chr(10).join(data_quality_lines) if data_quality_lines else '- None'}\n\n"

        f"OTHER ACTIVITY\n"
        f"{chr(10).join(activity_lines) if activity_lines else '- None'}\n\n"

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
        "maintenance": 3,
        "general_manager": 4,
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
