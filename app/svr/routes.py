from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import Store, SVRTemplateField, SVRReport, SVRReportValue, MaintenanceTicket

svr_bp = Blueprint("svr", __name__, url_prefix="/svr")


def get_supervisor_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def split_maintenance_lines(text: str):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def sync_maintenance_from_svr(report: SVRReport):
    maintenance_value = None

    for value in report.values:
        if value.field_key == "maintenance_needs":
            maintenance_value = (value.value_text or "").strip()
            break

    existing_tickets = MaintenanceTicket.query.filter_by(
        svr_report_id=report.id,
        source_type="svr"
    ).all()

    for ticket in existing_tickets:
        db.session.delete(ticket)

    maintenance_lines = split_maintenance_lines(maintenance_value)

    for line in maintenance_lines:
        db.session.add(
            MaintenanceTicket(
                store_number=report.store_number,
                title=line,
                details=f"Created from SVR #{report.id}",
                source_type="svr",
                svr_report_id=report.id,
                status="open",
            )
        )

    db.session.commit()


@svr_bp.route("/")
@login_required
@role_required("admin", "supervisor")
def index():
    visible_store_numbers = {store.store_number for store in get_supervisor_visible_stores()}

    reports = SVRReport.query.order_by(
        SVRReport.visit_date.desc(),
        SVRReport.created_at.desc()
    ).all()

    reports = [r for r in reports if r.store_number in visible_store_numbers]

    return render_template("svr_list.html", reports=reports)


@svr_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("supervisor")
def new_report():
    stores = get_supervisor_visible_stores()
    fields = SVRTemplateField.query.filter_by(is_active=True).order_by(
        SVRTemplateField.sort_order.asc(),
        SVRTemplateField.id.asc()
    ).all()

    if not stores:
        flash("No stores assigned to this supervisor.", "error")
        return redirect(url_for("svr.index"))

    default_store = stores[0].store_number
    selected_store = request.args.get("store", default_store).strip()

    allowed_store_numbers = {store.store_number for store in stores}
    if selected_store not in allowed_store_numbers:
        selected_store = default_store

    if request.method == "POST":
        store_number = request.form.get("store_number", "").strip()
        if store_number not in allowed_store_numbers:
            flash("Invalid store selection.", "error")
            return redirect(url_for("svr.new_report"))

        visit_date_raw = request.form.get("visit_date", "").strip()
        try:
            visit_date = datetime.strptime(visit_date_raw, "%Y-%m-%d").date() if visit_date_raw else date.today()
        except ValueError:
            flash("Invalid date.", "error")
            return redirect(url_for("svr.new_report", store=store_number))

        manager_on_duty = request.form.get("manager_on_duty", "").strip()

        report = SVRReport(
            store_number=store_number,
            visit_date=visit_date,
            manager_on_duty=manager_on_duty,
            supervisor_name=session.get("user_name"),
            created_by_user_id=session.get("user_id"),
        )
        db.session.add(report)
        db.session.flush()

        for field in fields:
            if field.field_key == "store_number":
                value_text = store_number
            elif field.field_key == "date":
                value_text = visit_date.strftime("%Y-%m-%d")
            elif field.field_key == "manager_on_duty":
                value_text = manager_on_duty
            else:
                value_text = request.form.get(field.field_key, "").strip()

            db.session.add(
                SVRReportValue(
                    report_id=report.id,
                    template_field_id=field.id,
                    field_key=field.field_key,
                    field_label=field.field_label,
                    field_type=field.field_type,
                    sort_order=field.sort_order,
                    value_text=value_text,
                )
            )

        db.session.commit()
        sync_maintenance_from_svr(report)

        flash("SVR saved successfully.", "success")
        return redirect(url_for("svr.view_report", report_id=report.id))

    return render_template(
        "svr_form.html",
        stores=stores,
        fields=fields,
        selected_store=selected_store,
        today=date.today().strftime("%Y-%m-%d"),
    )


@svr_bp.route("/<int:report_id>")
@login_required
@role_required("admin", "supervisor")
def view_report(report_id):
    report = SVRReport.query.get_or_404(report_id)
    visible_store_numbers = {store.store_number for store in get_supervisor_visible_stores()}

    if report.store_number not in visible_store_numbers:
        flash("You do not have access to that SVR.", "error")
        return redirect(url_for("svr.index"))

    values = sorted(report.values, key=lambda x: (x.sort_order, x.id))

    manager_summary = {
        "store_number": report.store_number,
        "visit_date": report.visit_date.strftime("%B %d, %Y"),
        "supervisor_name": report.supervisor_name or "—",
        "manager_on_duty": report.manager_on_duty or "—",
        "cleaning_list_for_week": "",
        "goals_for_week": "",
    }

    for value in values:
        if value.field_key == "cleaning_list_for_week":
            manager_summary["cleaning_list_for_week"] = value.value_text or ""
        elif value.field_key == "goals_for_week":
            manager_summary["goals_for_week"] = value.value_text or ""

    return render_template(
        "svr_view.html",
        report=report,
        values=values,
        manager_summary=manager_summary,
    )


@svr_bp.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "create":
            field_key = request.form.get("field_key", "").strip()
            field_label = request.form.get("field_label", "").strip()
            field_type = request.form.get("field_type", "textarea").strip()
            sort_order_raw = request.form.get("sort_order", "999").strip()

            if not field_key or not field_label:
                flash("Field key and label are required.", "error")
                return redirect(url_for("svr.admin"))

            try:
                sort_order = int(sort_order_raw)
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("svr.admin"))

            existing = SVRTemplateField.query.filter_by(field_key=field_key).first()
            if existing:
                flash("That field key already exists.", "error")
                return redirect(url_for("svr.admin"))

            db.session.add(
                SVRTemplateField(
                    field_key=field_key,
                    field_label=field_label,
                    field_type=field_type,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            db.session.commit()
            flash("SVR field created.", "success")
            return redirect(url_for("svr.admin"))

        if action == "update":
            field_id = request.form.get("field_id", "").strip()
            field = SVRTemplateField.query.get(field_id)

            if not field:
                flash("Field not found.", "error")
                return redirect(url_for("svr.admin"))

            field.field_key = request.form.get("field_key", "").strip()
            field.field_label = request.form.get("field_label", "").strip()
            field.field_type = request.form.get("field_type", "textarea").strip()

            try:
                field.sort_order = int(request.form.get("sort_order", "999").strip())
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("svr.admin"))

            field.is_active = request.form.get("is_active") == "on"

            db.session.commit()
            flash("SVR field updated.", "success")
            return redirect(url_for("svr.admin"))

    fields = SVRTemplateField.query.order_by(
        SVRTemplateField.sort_order.asc(),
        SVRTemplateField.id.asc()
    ).all()

    return render_template("svr_admin.html", fields=fields)