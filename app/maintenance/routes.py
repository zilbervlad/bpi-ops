from datetime import datetime, date, time, timedelta
from io import BytesIO

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import MaintenanceTicket, Store

maintenance_bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")


def get_current_role():
    return (session.get("user_role") or "").strip()


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role in ["admin", "maintenance"]:
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def get_visible_store_numbers():
    return {store.store_number for store in get_visible_stores()}


def split_lines_to_tasks(text: str):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def get_filtered_tickets():
    visible_stores = get_visible_stores()
    visible_store_numbers = {store.store_number for store in visible_stores}

    status_filter = request.args.get("status", "").strip()
    store_filter = request.args.get("store", "").strip()

    tickets = MaintenanceTicket.query.order_by(
        MaintenanceTicket.created_at.asc(),
        MaintenanceTicket.id.asc()
    ).all()

    tickets = [t for t in tickets if t.store_number in visible_store_numbers]

    if status_filter:
        tickets = [t for t in tickets if t.status == status_filter]

    if store_filter:
        tickets = [t for t in tickets if t.store_number == store_filter]

    return tickets, visible_stores, status_filter, store_filter


def autosize_worksheet_columns(worksheet):
    for column_cells in worksheet.columns:
        max_length = 0
        column_letter = get_column_letter(column_cells[0].column)

        for cell in column_cells:
            try:
                cell_value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(cell_value))
            except Exception:
                pass

        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 40)


def style_header_row(worksheet, row_number=1):
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    for cell in worksheet[row_number]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")


def create_maintenance_excel(tickets, status_filter, store_filter):
    wb = Workbook()

    summary_ws = wb.active
    summary_ws.title = "Summary"
    summary_ws.append(["Metric", "Value"])
    summary_ws.append(["Selected Store", store_filter or "All"])
    summary_ws.append(["Selected Status", status_filter or "All"])
    summary_ws.append(["Total Tickets", len(tickets)])

    style_header_row(summary_ws)
    autosize_worksheet_columns(summary_ws)

    tickets_ws = wb.create_sheet(title="Maintenance Tickets")
    tickets_ws.append([
        "Store",
        "Title",
        "Details",
        "Status",
        "Source Type",
        "Created At",
        "SVR Report ID",
    ])

    for ticket in tickets:
        created_at_display = (
            ticket.created_at.strftime("%Y-%m-%d %I:%M %p")
            if ticket.created_at else ""
        )

        tickets_ws.append([
            ticket.store_number,
            ticket.title or "",
            ticket.details or "",
            ticket.status.replace("_", " ").title() if ticket.status else "",
            (ticket.source_type or "").upper(),
            created_at_display,
            ticket.svr_report_id if ticket.svr_report_id is not None else "",
        ])

    style_header_row(tickets_ws)
    autosize_worksheet_columns(tickets_ws)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output



def parse_optional_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_optional_time(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def parse_optional_int(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except ValueError:
        return None


def normalize_priority(value):
    value = (value or "normal").strip().lower()
    valid_priorities = {"low", "normal", "high", "urgent"}
    return value if value in valid_priorities else "normal"


def format_maintenance_time(ticket):
    if not ticket or not getattr(ticket, "scheduled_time", None):
        return "Any Time"

    try:
        return ticket.scheduled_time.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return "Any Time"


def build_calendar_time_slots():
    slots = []
    for hour in range(7, 20):
        slot_time = time(hour=hour, minute=0)
        slots.append({
            "value": slot_time.strftime("%H:%M"),
            "label": slot_time.strftime("%I:%M %p").lstrip("0"),
            "time": slot_time,
            "tickets": [],
        })
    return slots


def get_calendar_week_start():
    raw_start = request.args.get("start", "").strip()
    requested = parse_optional_date(raw_start) or date.today()
    return requested - timedelta(days=requested.weekday())


def visible_ticket_or_none(ticket_id, visible_store_numbers):
    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return None

    ticket = MaintenanceTicket.query.get(ticket_id)
    if not ticket:
        return None

    if ticket.store_number not in visible_store_numbers:
        return None

    return ticket


def apply_schedule_form_to_ticket(ticket):
    ticket.store_number = request.form.get("store_number", ticket.store_number).strip()
    ticket.title = request.form.get("title", ticket.title or "").strip()
    ticket.details = request.form.get("details", ticket.details or "").strip()
    ticket.status = request.form.get("status", ticket.status or "open").strip()

    ticket.assigned_to = request.form.get("assigned_to", ticket.assigned_to or "").strip() or None
    ticket.scheduled_date = parse_optional_date(request.form.get("scheduled_date"))
    ticket.scheduled_time = parse_optional_time(request.form.get("scheduled_time"))
    ticket.estimated_minutes = parse_optional_int(request.form.get("estimated_minutes"))
    ticket.priority = normalize_priority(request.form.get("priority"))

    valid_statuses = {"open", "assigned", "in_progress", "complete"}
    if ticket.status not in valid_statuses:
        ticket.status = "open"


@maintenance_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor", "maintenance", "manager")
def index():
    visible_stores = get_visible_stores()
    visible_store_numbers = get_visible_store_numbers()
    role = get_current_role()

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "create":
            store_number = request.form.get("store_number", "").strip()
            title = request.form.get("title", "").strip()
            details = request.form.get("details", "").strip()

            if store_number not in visible_store_numbers:
                flash("Invalid store selection.", "error")
                return redirect(url_for("maintenance.index"))

            title_lines = split_lines_to_tasks(title)

            if title_lines:
                for line in title_lines:
                    ticket = MaintenanceTicket(
                        store_number=store_number,
                        title=line,
                        details=details,
                        source_type="manual",
                        status="open",
                    )
                    db.session.add(ticket)

                db.session.commit()

                if len(title_lines) == 1:
                    flash("Maintenance task created.", "success")
                else:
                    flash(f"{len(title_lines)} maintenance tasks created.", "success")

                return redirect(url_for("maintenance.index"))

            if not details:
                flash("Task title is required.", "error")
                return redirect(url_for("maintenance.index"))

            ticket = MaintenanceTicket(
                store_number=store_number,
                title="General maintenance task",
                details=details,
                source_type="manual",
                status="open",
            )
            db.session.add(ticket)
            db.session.commit()

            flash("Maintenance task created.", "success")
            return redirect(url_for("maintenance.index"))

        if action == "update":
            ticket_id = request.form.get("ticket_id", "").strip()
            ticket = MaintenanceTicket.query.get(ticket_id)

            if not ticket:
                flash("Ticket not found.", "error")
                return redirect(url_for("maintenance.index"))

            if ticket.store_number not in visible_store_numbers:
                flash("You do not have access to that ticket.", "error")
                return redirect(url_for("maintenance.index"))

            store_number = request.form.get("store_number", "").strip()
            title = request.form.get("title", "").strip()
            details = request.form.get("details", "").strip()
            status = request.form.get("status", "").strip()

            valid_statuses = {"open", "assigned", "in_progress", "complete"}

            if store_number not in visible_store_numbers:
                flash("Invalid store selection.", "error")
                return redirect(url_for("maintenance.index"))

            if not title:
                flash("Task title is required.", "error")
                return redirect(url_for("maintenance.index"))

            if status not in valid_statuses:
                flash("Invalid status.", "error")
                return redirect(url_for("maintenance.index"))

            if role == "manager" and store_number != session.get("user_store"):
                flash("Managers can only manage tickets for their own store.", "error")
                return redirect(url_for("maintenance.index"))

            ticket.store_number = store_number
            ticket.title = title
            ticket.details = details
            ticket.status = status

            db.session.commit()
            flash("Maintenance task updated.", "success")
            return redirect(url_for("maintenance.index"))

        if action == "delete":
            if role == "manager":
                flash("Managers cannot delete maintenance tasks.", "error")
                return redirect(url_for("maintenance.index"))

            ticket_id = request.form.get("ticket_id", "").strip()
            ticket = MaintenanceTicket.query.get(ticket_id)

            if not ticket:
                flash("Ticket not found.", "error")
                return redirect(url_for("maintenance.index"))

            if ticket.store_number not in visible_store_numbers:
                flash("You do not have access to that ticket.", "error")
                return redirect(url_for("maintenance.index"))

            db.session.delete(ticket)
            db.session.commit()

            flash("Maintenance task deleted.", "success")
            return redirect(url_for("maintenance.index"))

    status_filter = request.args.get("status", "").strip()
    store_filter = request.args.get("store", "").strip()

    tickets = MaintenanceTicket.query.order_by(
        MaintenanceTicket.created_at.asc(),
        MaintenanceTicket.id.asc()
    ).all()

    tickets = [t for t in tickets if t.store_number in visible_store_numbers]

    if status_filter:
        tickets = [t for t in tickets if t.status == status_filter]

    if store_filter:
        tickets = [t for t in tickets if t.store_number == store_filter]

    open_tickets = sorted(
        [t for t in tickets if t.status == "open"],
        key=lambda t: (t.created_at or datetime.min, t.id or 0)
    )

    assigned_tickets = sorted(
        [t for t in tickets if t.status == "assigned"],
        key=lambda t: (t.created_at or datetime.min, t.id or 0)
    )

    in_progress_tickets = sorted(
        [t for t in tickets if t.status == "in_progress"],
        key=lambda t: (t.created_at or datetime.min, t.id or 0),
        reverse=True
    )

    complete_tickets = sorted(
        [t for t in tickets if t.status == "complete"],
        key=lambda t: (t.created_at or datetime.min, t.id or 0),
        reverse=True
    )

    return render_template(
        "maintenance.html",
        tickets=tickets,
        open_tickets=open_tickets,
        assigned_tickets=assigned_tickets,
        in_progress_tickets=in_progress_tickets,
        complete_tickets=complete_tickets,
        stores=visible_stores,
        status_filter=status_filter,
        store_filter=store_filter,
        user_role=role,
    )



@maintenance_bp.route("/calendar", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor", "maintenance", "manager")
def calendar():
    visible_stores = get_visible_stores()
    visible_store_numbers = get_visible_store_numbers()
    role = get_current_role()

    week_start = get_calendar_week_start()
    calendar_start_raw = request.form.get("calendar_start", "").strip()
    if calendar_start_raw:
        posted_week_start = parse_optional_date(calendar_start_raw)
        if posted_week_start:
            week_start = posted_week_start

    week_end = week_start + timedelta(days=6)
    previous_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "unschedule":
            if role == "manager":
                flash("Managers can view the maintenance calendar, but cannot schedule maintenance tasks.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            ticket = visible_ticket_or_none(request.form.get("ticket_id"), visible_store_numbers)
            if not ticket:
                flash("Maintenance task not found or not available.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            ticket.scheduled_date = None
            ticket.scheduled_time = None
            ticket.assigned_to = None

            db.session.commit()
            flash("Maintenance task moved back to unscheduled.", "success")
            return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

        if action == "unschedule_all":
            if role == "manager":
                flash("Managers can view the maintenance calendar, but cannot schedule maintenance tasks.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            scheduled_tickets = MaintenanceTicket.query.filter(
                MaintenanceTicket.store_number.in_(visible_store_numbers),
                MaintenanceTicket.scheduled_date.isnot(None),
                MaintenanceTicket.status != "complete"
            ).all()

            for ticket in scheduled_tickets:
                ticket.scheduled_date = None
                ticket.scheduled_time = None
                ticket.assigned_to = None

            db.session.commit()
            flash(f"{len(scheduled_tickets)} maintenance tasks moved back to Unscheduled.", "success")
            return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

        if action == "schedule":
            if role == "manager":
                flash("Managers can view the maintenance calendar, but cannot schedule maintenance tasks.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            ticket = visible_ticket_or_none(request.form.get("ticket_id"), visible_store_numbers)
            if not ticket:
                flash("Maintenance task not found or not available.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            new_store_number = request.form.get("store_number", ticket.store_number).strip()
            if new_store_number not in visible_store_numbers:
                flash("Invalid store selection.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            title = request.form.get("title", "").strip()
            if not title:
                flash("Task title is required.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            apply_schedule_form_to_ticket(ticket)

            if ticket.scheduled_time and not ticket.scheduled_date:
                flash("Please choose a scheduled date when setting a scheduled time.", "error")
                return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

            db.session.commit()
            flash("Maintenance schedule updated.", "success")
            return redirect(url_for("maintenance.calendar", start=week_start.strftime("%Y-%m-%d")))

    tickets = MaintenanceTicket.query.order_by(
        MaintenanceTicket.created_at.asc(),
        MaintenanceTicket.id.asc()
    ).all()

    tickets = [
        t for t in tickets
        if t.store_number in visible_store_numbers
    ]

    def ticket_sort_key(ticket):
        return (
            ticket.scheduled_date or date.max,
            ticket.scheduled_time or time(hour=23, minute=59),
            ticket.created_at or datetime.min,
            ticket.id or 0,
        )

    tickets = sorted(tickets, key=ticket_sort_key)

    days = []
    for offset in range(7):
        current_day = week_start + timedelta(days=offset)
        day_tickets = [t for t in tickets if t.scheduled_date == current_day]
        any_time_tickets = [t for t in day_tickets if not t.scheduled_time]

        slots = build_calendar_time_slots()
        for slot in slots:
            slot["tickets"] = [
                t for t in day_tickets
                if t.scheduled_time and t.scheduled_time.hour == slot["time"].hour
            ]

        days.append({
            "date": current_day,
            "tickets": day_tickets,
            "any_time_tickets": any_time_tickets,
            "slots": slots,
        })

    # Keep completed tasks visible on the calendar when scheduled,
    # but do not keep completed unscheduled work in the dispatch pile.
    unscheduled_tickets = [
        t for t in tickets
        if not t.scheduled_date and t.status != "complete"
    ]

    return render_template(
        "maintenance_calendar.html",
        stores=visible_stores,
        days=days,
        unscheduled_tickets=unscheduled_tickets,
        week_start=week_start,
        week_end=week_end,
        previous_week=previous_week,
        next_week=next_week,
        user_role=role,
        format_time=format_maintenance_time,
    )


@maintenance_bp.route("/calendar/move", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "maintenance")
def move_calendar_ticket():
    visible_store_numbers = get_visible_store_numbers()

    ticket = visible_ticket_or_none(request.form.get("ticket_id"), visible_store_numbers)
    if not ticket:
        return jsonify({"ok": False, "message": "Task not found or access denied."}), 404

    scheduled_date_raw = request.form.get("scheduled_date", "").strip()
    scheduled_time_raw = request.form.get("scheduled_time", "").strip()

    if scheduled_date_raw == "unscheduled":
        ticket.scheduled_date = None
        ticket.scheduled_time = None
        db.session.commit()
        return jsonify({"ok": True, "message": "Task moved to unscheduled."})

    scheduled_date = parse_optional_date(scheduled_date_raw)
    if not scheduled_date:
        return jsonify({"ok": False, "message": "Invalid scheduled date."}), 400

    ticket.scheduled_date = scheduled_date
    ticket.scheduled_time = parse_optional_time(scheduled_time_raw)

    db.session.commit()
    return jsonify({"ok": True, "message": "Maintenance task moved."})


@maintenance_bp.route("/export/excel")
@login_required
@role_required("admin", "supervisor", "maintenance", "manager")
def export_excel():
    tickets, _, status_filter, store_filter = get_filtered_tickets()
    workbook_stream = create_maintenance_excel(tickets, status_filter, store_filter)

    filename_parts = ["maintenance_export"]
    if store_filter:
        filename_parts.append(store_filter)
    if status_filter:
        filename_parts.append(status_filter)

    filename = "_".join(filename_parts) + ".xlsx"

    return send_file(
        workbook_stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )