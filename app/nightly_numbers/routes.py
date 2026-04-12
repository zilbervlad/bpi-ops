from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import NightlyNumbersReport, NightlyNumbersFieldConfig, Store, User
from app.services.email_service import send_email

nightly_numbers_bp = Blueprint("nightly_numbers", __name__, url_prefix="/nightly-numbers")

APP_TZ = ZoneInfo("America/New_York")

DEFAULT_FIELD_CONFIG = [
    {
        "field_key": "manager_name",
        "field_label": "Your Name",
        "field_type": "text",
        "sort_order": 0,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "royalty_sales",
        "field_label": "Royalty Sales",
        "field_type": "text",
        "sort_order": 1,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "variable_labor",
        "field_label": "Variable Labor",
        "field_type": "text",
        "sort_order": 2,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "labor_goal",
        "field_label": "Labor Goal",
        "field_type": "text",
        "sort_order": 3,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "invoices_transfers_checked",
        "field_label": "Invoices / Transfers Checked",
        "field_type": "checkbox",
        "sort_order": 4,
        "is_enabled": True,
        "is_required": False,
    },
    {
        "field_key": "food_variance",
        "field_label": "Food Variance",
        "field_type": "text",
        "sort_order": 5,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "food_variance_details",
        "field_label": "Food Variance Details",
        "field_type": "textarea",
        "sort_order": 6,
        "is_enabled": True,
        "is_required": False,
    },
    {
        "field_key": "adt",
        "field_label": "ADT",
        "field_type": "text",
        "sort_order": 7,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "adt_reason",
        "field_label": "ADT Above 25 Min - Why?",
        "field_type": "textarea",
        "sort_order": 8,
        "is_enabled": True,
        "is_required": False,
    },
    {
        "field_key": "load_time",
        "field_label": "Load Time",
        "field_type": "text",
        "sort_order": 9,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "bad_orders",
        "field_label": "Bad Orders - Record Order #",
        "field_type": "textarea",
        "sort_order": 10,
        "is_enabled": True,
        "is_required": False,
    },
    {
        "field_key": "cash_diff",
        "field_label": "Cash +/-",
        "field_type": "text",
        "sort_order": 11,
        "is_enabled": True,
        "is_required": True,
    },
    {
        "field_key": "food_order_placed",
        "field_label": "Food Order Placed",
        "field_type": "checkbox",
        "sort_order": 12,
        "is_enabled": True,
        "is_required": False,
    },
]

FIELD_META = {
    "manager_name": {
        "placeholder": "Enter your name",
    },
    "royalty_sales": {
        "placeholder": "Example: 8249.55",
    },
    "variable_labor": {
        "placeholder": "Example: 20.00",
    },
    "labor_goal": {
        "placeholder": "Example: 21.0",
        "default": "21.0",
    },
    "food_variance": {
        "placeholder": "Example: 0.01",
    },
    "food_variance_details": {
        "placeholder": "Explain variances if needed",
        "rows": 3,
    },
    "adt": {
        "placeholder": "Example: 27.83",
    },
    "adt_reason": {
        "placeholder": "Explain if ADT was above target",
        "rows": 3,
    },
    "load_time": {
        "placeholder": "Example: 04:59",
    },
    "bad_orders": {
        "placeholder": "Order numbers or notes",
        "rows": 3,
    },
    "cash_diff": {
        "placeholder": "Example: -11.53",
    },
}


def ensure_field_config_seeded():
    existing = {
        field.field_key: field
        for field in NightlyNumbersFieldConfig.query.all()
    }

    changed = False

    for field_def in DEFAULT_FIELD_CONFIG:
        if field_def["field_key"] not in existing:
            db.session.add(NightlyNumbersFieldConfig(**field_def))
            changed = True

    if changed:
        db.session.commit()


def get_field_config():
    ensure_field_config_seeded()
    return NightlyNumbersFieldConfig.query.order_by(
        NightlyNumbersFieldConfig.sort_order.asc(),
        NightlyNumbersFieldConfig.id.asc()
    ).all()


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


def get_report_value(report, field_key):
    if not report:
        return None
    return getattr(report, field_key, None)


def apply_form_value_to_report(report, field):
    field_key = field.field_key

    if field.field_type == "checkbox":
        setattr(report, field_key, request.form.get(field_key) == "on")
        return

    raw_value = request.form.get(field_key, "").strip()

    if field_key in [
        "royalty_sales",
        "variable_labor",
        "labor_goal",
        "food_variance",
        "adt",
        "cash_diff",
    ]:
        setattr(report, field_key, parse_float(raw_value))
        return

    setattr(report, field_key, raw_value or None)


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

    fields = get_field_config()
    today_str = datetime.now(APP_TZ).strftime("%Y-%m-%d")

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

        for field in fields:
            apply_form_value_to_report(report, field)

        db.session.commit()

        try:
            email_result = send_nightly_numbers_email(report)
            flash(
                f"Nightly numbers saved and emailed to {email_result['manager_email']}.",
                "success"
            )
        except Exception as e:
            flash(f"Nightly numbers saved, but email failed: {str(e)}", "error")

        return redirect(url_for("nightly_numbers.index", reset=1))

    reset = request.args.get("reset")
    existing_report = None

    if not reset:
        existing_report = NightlyNumbersReport.query.filter_by(
            store_number=user_store,
            report_date=datetime.strptime(today_str, "%Y-%m-%d").date()
        ).first()

    field_values = {}
    for field in fields:
        value = get_report_value(existing_report, field.field_key)

        if value is None and field.field_key in FIELD_META and "default" in FIELD_META[field.field_key]:
            value = FIELD_META[field.field_key]["default"]

        field_values[field.field_key] = value

    return render_template(
        "nightly_numbers.html",
        report=existing_report,
        today_str=today_str,
        store_number=user_store,
        fields=fields,
        field_values=field_values,
        field_meta=FIELD_META,
    )


@nightly_numbers_bp.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor")
def admin():
    fields = get_field_config()

    if request.method == "POST":
        if session.get("user_role") != "admin":
            flash("Only admins can update nightly form settings.", "error")
            return redirect(url_for("nightly_numbers.admin"))

        for field in fields:
            field.field_label = request.form.get(
                f"label_{field.id}",
                field.field_label
            ).strip() or field.field_label

            field.is_enabled = request.form.get(f"enabled_{field.id}") == "on"
            field.is_required = request.form.get(f"required_{field.id}") == "on"

        db.session.commit()
        flash("Nightly form settings updated.", "success")
        return redirect(url_for("nightly_numbers.admin"))

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
        fields=fields,
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

    return render_template(
        "nightly_numbers_edit.html",
        report=report,
    )