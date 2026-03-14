from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import MaintenanceTicket, Store

maintenance_bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")


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


@maintenance_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor", "maintenance")
def index():
    visible_stores = get_visible_stores()
    visible_store_numbers = get_visible_store_numbers()

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

            ticket.store_number = store_number
            ticket.title = title
            ticket.details = details
            ticket.status = status

            db.session.commit()
            flash("Maintenance task updated.", "success")
            return redirect(url_for("maintenance.index"))

        if action == "delete":
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
        MaintenanceTicket.created_at.desc(),
        MaintenanceTicket.id.desc()
    ).all()

    tickets = [t for t in tickets if t.store_number in visible_store_numbers]

    if status_filter:
        tickets = [t for t in tickets if t.status == status_filter]

    if store_filter:
        tickets = [t for t in tickets if t.store_number == store_filter]

    return render_template(
        "maintenance.html",
        tickets=tickets,
        stores=visible_stores,
        status_filter=status_filter,
        store_filter=store_filter,
    )