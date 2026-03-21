from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import NightlyNumbersReport, Store, User
from app.services.email_service import send_email

nightly_numbers_bp = Blueprint("nightly_numbers", __name__, url_prefix="/nightly-numbers")


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role == "admin":
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


def parse_float(value):
    value = (value or "").strip().replace(",", "")
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def send_nightly_numbers_email(report: NightlyNumbersReport):
    manager_user = User.query.filter_by(
        store_number=report.store_number,
        role="manager",
        is_active=True
    ).first()

    manager_email = manager_user.get_notification_email() if manager_user else None

    store = Store.query.filter_by(store_number=report.store_number).first()

    supervisor = None
    if store:
        supervisor = User.query.filter_by(
            area_name=store.area_name,
            role="supervisor",
            is_active=True
        ).first()

    supervisor_email = supervisor.get_notification_email() if supervisor else None

    admin_users = User.query.filter_by(role="admin", is_active=True).all()
    admin_emails = []
    for admin in admin_users:
        email = admin.get_notification_email()
        if email:
            admin_emails.append(email)

    cc_emails = []
    if supervisor_email:
        cc_emails.append(supervisor_email)
    cc_emails.extend(admin_emails)

    # remove duplicates and remove manager if duplicated in cc
    cc_emails = [email for email in dict.fromkeys(cc_emails) if email and email != manager_email]

    if not manager_email:
        raise ValueError(f"No manager notification email configured for store {report.store_number}.")

    labor_status = ""
    if report.variable_labor is not None and report.labor_goal is not None:
        diff = round(report.variable_labor - report.labor_goal, 2)
        if diff > 0:
            labor_status = f"Above goal by {diff}"
        elif diff < 0:
            labor_status = f"Below goal by {abs(diff)}"
        else:
            labor_status = "On goal"

    body = (
        f"Nightly Numbers Report\n"
        f"Store: {report.store_number}\n"
        f"Date: {report.report_date.strftime('%B %d, %Y')}\n"
        f"Manager: {report.manager_name or 'Not provided'}\n\n"
        f"Royalty Sales: {report.royalty_sales if report.royalty_sales is not None else 'Not provided'}\n"
        f"Variable Labor: {report.variable_labor if report.variable_labor is not None else 'Not provided'}\n"
        f"Labor Goal: {report.labor_goal if report.labor_goal is not None else 'Not provided'}\n"
        f"Labor Status: {labor_status or 'Not available'}\n"
        f"Invoices/Transfers Checked: {'Yes' if report.invoices_transfers_checked else 'No'}\n"
        f"Food Variance: {report.food_variance if report.food_variance is not None else 'Not provided'}\n"
        f"Food Variance Details: {report.food_variance_details or 'None'}\n"
        f"ADT: {report.adt if report.adt is not None else 'Not provided'}\n"
        f"ADT Reason: {report.adt_reason or 'None'}\n"
        f"Load Time: {report.load_time or 'Not provided'}\n"
        f"Bad Orders: {report.bad_orders or 'None'}\n"
        f"Cash +/-: {report.cash_diff if report.cash_diff is not None else 'Not provided'}\n"
        f"Food Order Placed: {'Yes' if report.food_order_placed else 'No'}\n\n"
        f"- BPI Ops"
    )

    send_email(
        to_email=manager_email,
        subject=f"Store {report.store_number} Nightly Numbers - {report.report_date.strftime('%b %d, %Y')}",
        body=body,
        cc_emails=cc_emails if cc_emails else None
    )

    return {
        "manager_email": manager_email,
        "supervisor_email": supervisor_email,
        "admin_emails": admin_emails,
    }


@nightly_numbers_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("manager", "admin", "supervisor")
def index():
    role = session.get("user_role")
    user_store = session.get("user_store")

    if role != "manager":
        return redirect(url_for("nightly_numbers.admin"))

    if not user_store:
        flash("No store is assigned to this manager.", "error")
        return redirect(url_for("dashboard.home"))

    today_str = datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        report_date_str = request.form.get("report_date", "").strip()

        try:
            report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid report date.", "error")
            return redirect(url_for("nightly_numbers.index"))

        report = NightlyNumbersReport.query.filter_by(
            store_number=user_store,
            report_date=report_date
        ).first()

        if not report:
            report = NightlyNumbersReport(
                store_number=user_store,
                report_date=report_date,
                created_by_user_id=session.get("user_id")
            )
            db.session.add(report)

        report.manager_name = request.form.get("manager_name", "").strip() or None
        report.royalty_sales = parse_float(request.form.get("royalty_sales"))
        report.variable_labor = parse_float(request.form.get("variable_labor"))
        report.labor_goal = parse_float(request.form.get("labor_goal"))
        report.invoices_transfers_checked = request.form.get("invoices_transfers_checked") == "on"
        report.food_variance = parse_float(request.form.get("food_variance"))
        report.food_variance_details = request.form.get("food_variance_details", "").strip() or None
        report.adt = parse_float(request.form.get("adt"))
        report.adt_reason = request.form.get("adt_reason", "").strip() or None
        report.load_time = request.form.get("load_time", "").strip() or None
        report.bad_orders = request.form.get("bad_orders", "").strip() or None
        report.cash_diff = parse_float(request.form.get("cash_diff"))
        report.food_order_placed = request.form.get("food_order_placed") == "on"

        db.session.commit()

        try:
            email_result = send_nightly_numbers_email(report)
            flash(
                f"Nightly numbers saved and emailed to {email_result['manager_email']}.",
                "success"
            )
        except Exception as e:
            flash(f"Nightly numbers saved, but email failed: {str(e)}", "error")

        return redirect(url_for("nightly_numbers.index"))

    existing_report = NightlyNumbersReport.query.filter_by(
        store_number=user_store,
        report_date=datetime.strptime(today_str, "%Y-%m-%d").date()
    ).first()

    return render_template(
        "nightly_numbers.html",
        report=existing_report,
        today_str=today_str,
        store_number=user_store,
    )


@nightly_numbers_bp.route("/admin", methods=["GET"])
@login_required
@role_required("admin", "supervisor")
def admin():
    visible_stores = get_visible_stores()
    visible_store_numbers = {store.store_number for store in visible_stores}

    selected_store = request.args.get("store", "").strip()
    selected_date = request.args.get("date", "").strip()

    query = NightlyNumbersReport.query

    if selected_store:
        query = query.filter_by(store_number=selected_store)

    if selected_date:
        try:
            parsed_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
            query = query.filter_by(report_date=parsed_date)
        except ValueError:
            flash("Invalid date filter ignored.", "error")

    reports = query.order_by(
        NightlyNumbersReport.report_date.desc(),
        NightlyNumbersReport.store_number.asc()
    ).all()

    reports = [r for r in reports if r.store_number in visible_store_numbers]

    return render_template(
        "nightly_numbers_admin.html",
        reports=reports,
        stores=visible_stores,
        selected_store=selected_store,
        selected_date=selected_date,
    )


@nightly_numbers_bp.route("/admin/<int:report_id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_report(report_id):
    report = NightlyNumbersReport.query.get_or_404(report_id)

    if request.method == "POST":
        report_date_str = request.form.get("report_date", "").strip()

        try:
            report.report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid report date.", "error")
            return redirect(url_for("nightly_numbers.edit_report", report_id=report.id))

        report.manager_name = request.form.get("manager_name", "").strip() or None
        report.royalty_sales = parse_float(request.form.get("royalty_sales"))
        report.variable_labor = parse_float(request.form.get("variable_labor"))
        report.labor_goal = parse_float(request.form.get("labor_goal"))
        report.invoices_transfers_checked = request.form.get("invoices_transfers_checked") == "on"
        report.food_variance = parse_float(request.form.get("food_variance"))
        report.food_variance_details = request.form.get("food_variance_details", "").strip() or None
        report.adt = parse_float(request.form.get("adt"))
        report.adt_reason = request.form.get("adt_reason", "").strip() or None
        report.load_time = request.form.get("load_time", "").strip() or None
        report.bad_orders = request.form.get("bad_orders", "").strip() or None
        report.cash_diff = parse_float(request.form.get("cash_diff"))
        report.food_order_placed = request.form.get("food_order_placed") == "on"

        db.session.commit()
        flash("Nightly numbers report updated.", "success")
        return redirect(url_for("nightly_numbers.admin"))

    return render_template("nightly_numbers_edit.html", report=report)