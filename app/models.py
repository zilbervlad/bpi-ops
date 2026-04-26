from datetime import datetime, date
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db

APP_TZ = ZoneInfo("America/New_York")


def today_et():
    return datetime.now(APP_TZ).date()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="manager")

    area_name = db.Column(db.String(100), nullable=True)
    store_number = db.Column(db.String(10), nullable=True)

    email = db.Column(db.String(255), nullable=True)
    notification_email = db.Column(db.String(255), nullable=True)
    email_enabled = db.Column(db.Boolean, nullable=False, default=True)

    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"

    def is_supervisor(self):
        return self.role == "supervisor"

    def is_manager(self):
        return self.role == "manager"

    def is_maintenance(self):
        return self.role == "maintenance"

    def get_notification_email(self):
        if not self.email_enabled:
            return None
        return self.notification_email or self.email


class Store(db.Model):
    __tablename__ = "stores"

    id = db.Column(db.Integer, primary_key=True)
    store_number = db.Column(db.String(10), unique=True, nullable=False)
    store_name = db.Column(db.String(120), nullable=True)
    area_name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class ChecklistTemplateItem(db.Model):
    __tablename__ = "checklist_template_items"

    id = db.Column(db.Integer, primary_key=True)
    section_name = db.Column(db.String(120), nullable=False)
    task_text = db.Column(db.String(255), nullable=False)
    expected_minutes = db.Column(db.Integer, nullable=False, default=0)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_required = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)


class DailyChecklist(db.Model):
    __tablename__ = "daily_checklists"

    id = db.Column(db.Integer, primary_key=True)
    store_number = db.Column(db.String(20), nullable=False)
    checklist_date = db.Column(db.Date, nullable=False, default=today_et)

    manager_on_duty = db.Column(db.String(120), nullable=True)
    opening_manager = db.Column(db.String(120), nullable=True)
    closing_manager = db.Column(db.String(120), nullable=True)

    status = db.Column(db.String(50), nullable=False, default="in_progress")
    percent_complete = db.Column(db.Float, nullable=False, default=0.0)

    integrity_score = db.Column(db.Float, nullable=False, default=0.0)
    integrity_possible = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship(
        "DailyChecklistItem",
        backref="daily_checklist",
        lazy=True,
        cascade="all, delete-orphan"
    )


class DailyChecklistItem(db.Model):
    __tablename__ = "daily_checklist_items"

    id = db.Column(db.Integer, primary_key=True)

    daily_checklist_id = db.Column(
        db.Integer,
        db.ForeignKey("daily_checklists.id"),
        nullable=False
    )

    template_item_id = db.Column(
        db.Integer,
        db.ForeignKey("checklist_template_items.id"),
        nullable=False
    )

    section_name = db.Column(db.String(120), nullable=False)
    task_text = db.Column(db.String(255), nullable=False)
    expected_minutes = db.Column(db.Integer, nullable=False, default=0)
    is_required = db.Column(db.Boolean, default=True)

    is_completed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    template_item = db.relationship("ChecklistTemplateItem")


class ChecklistException(db.Model):
    __tablename__ = "checklist_exceptions"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)
    checklist_date = db.Column(db.Date, nullable=False)

    manager_on_duty = db.Column(db.String(120), nullable=True)

    checklist_started = db.Column(db.Boolean, default=False)
    checklist_completed = db.Column(db.Boolean, default=False)
    manager_walk_missed = db.Column(db.Boolean, default=False)

    percent_complete = db.Column(db.Float, nullable=False, default=0.0)
    integrity_score = db.Column(db.Float, nullable=False, default=0.0)

    incomplete_task_count = db.Column(db.Integer, nullable=False, default=0)
    incomplete_task_names = db.Column(db.Text, nullable=True)

    auto_closed_at = db.Column(db.DateTime, default=datetime.utcnow)
    closeout_type = db.Column(db.String(50), nullable=False, default="auto_5am")


class IntegritySettings(db.Model):
    __tablename__ = "integrity_settings"

    id = db.Column(db.Integer, primary_key=True)

    integrity_section = db.Column(
        db.String(120),
        nullable=False,
        default="Before Open / Before 10:30"
    )

    completion_weight = db.Column(db.Float, nullable=False, default=0.60)
    timing_weight = db.Column(db.Float, nullable=False, default=0.40)

    burst_threshold = db.Column(db.Integer, nullable=False, default=4)
    burst_window_seconds = db.Column(db.Integer, nullable=False, default=60)

    full_score_ratio = db.Column(db.Float, nullable=False, default=0.70)
    medium_score_ratio = db.Column(db.Float, nullable=False, default=0.50)
    low_score_ratio = db.Column(db.Float, nullable=False, default=0.30)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SVRTemplateField(db.Model):
    __tablename__ = "svr_template_fields"

    id = db.Column(db.Integer, primary_key=True)
    field_key = db.Column(db.String(100), unique=True, nullable=False)
    field_label = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), nullable=False, default="textarea")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)


class SVRReport(db.Model):
    __tablename__ = "svr_reports"

    id = db.Column(db.Integer, primary_key=True)
    store_number = db.Column(db.String(10), nullable=False)
    visit_date = db.Column(db.Date, nullable=False, default=today_et)
    manager_on_duty = db.Column(db.String(120), nullable=True)

    supervisor_name = db.Column(db.String(120), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User")

    values = db.relationship(
        "SVRReportValue",
        backref="report",
        lazy=True,
        cascade="all, delete-orphan"
    )


class SVRReportValue(db.Model):
    __tablename__ = "svr_report_values"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("svr_reports.id"), nullable=False)
    template_field_id = db.Column(db.Integer, db.ForeignKey("svr_template_fields.id"), nullable=False)

    field_key = db.Column(db.String(100), nullable=False)
    field_label = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), nullable=False, default="textarea")
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    value_text = db.Column(db.Text, nullable=True)

    template_field = db.relationship("SVRTemplateField")


class WeeklyFocusItem(db.Model):
    __tablename__ = "weekly_focus_items"

    id = db.Column(db.Integer, primary_key=True)
    store_number = db.Column(db.String(10), nullable=False)

    item_type = db.Column(db.String(50), nullable=False)
    item_text = db.Column(db.String(255), nullable=False)

    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    source_type = db.Column(db.String(50), nullable=False, default="svr")
    svr_report_id = db.Column(db.Integer, db.ForeignKey("svr_reports.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    svr_report = db.relationship("SVRReport")


class MaintenanceTicket(db.Model):
    __tablename__ = "maintenance_tickets"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    details = db.Column(db.Text, nullable=True)

    source_type = db.Column(db.String(50), nullable=False, default="manual")
    svr_report_id = db.Column(db.Integer, db.ForeignKey("svr_reports.id"), nullable=True)

    status = db.Column(db.String(50), nullable=False, default="open")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    svr_report = db.relationship("SVRReport")


class NightlyNumbersReport(db.Model):
    __tablename__ = "nightly_numbers_reports"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)
    report_date = db.Column(db.Date, nullable=False, default=today_et)

    manager_name = db.Column(db.String(120), nullable=True)

    royalty_sales = db.Column(db.Float, nullable=True)
    variable_labor = db.Column(db.Float, nullable=True)
    labor_goal = db.Column(db.Float, nullable=True)

    invoices_transfers_checked = db.Column(db.Boolean, default=False)

    food_variance = db.Column(db.Float, nullable=True)
    food_variance_details = db.Column(db.Text, nullable=True)

    adt = db.Column(db.Float, nullable=True)
    adt_reason = db.Column(db.Text, nullable=True)

    load_time = db.Column(db.String(20), nullable=True)
    bad_orders = db.Column(db.Text, nullable=True)

    cash_diff = db.Column(db.Float, nullable=True)
    food_order_placed = db.Column(db.Boolean, default=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User")


class NightlyNumbersFieldConfig(db.Model):
    __tablename__ = "nightly_numbers_field_config"

    id = db.Column(db.Integer, primary_key=True)

    field_key = db.Column(db.String(100), unique=True, nullable=False)
    field_label = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), nullable=False, default="text")
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    is_enabled = db.Column(db.Boolean, default=True)
    is_required = db.Column(db.Boolean, default=False)


class CashLog(db.Model):
    __tablename__ = "cash_logs"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)
    log_date = db.Column(db.Date, nullable=False, default=today_et)

    shift_type = db.Column(db.String(20), nullable=False)

    back_till = db.Column(db.Float, nullable=True)
    front_till = db.Column(db.Float, nullable=True)
    driver_banks = db.Column(db.Float, nullable=True)
    total_cash = db.Column(db.Float, nullable=True)

    amount_to_account_for = db.Column(db.Float, nullable=True)
    cash_over_short = db.Column(db.Float, nullable=True)

    manager_name = db.Column(db.String(120), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class VerificationTemplateField(db.Model):
    __tablename__ = "verification_template_fields"

    id = db.Column(db.Integer, primary_key=True)
    field_key = db.Column(db.String(100), unique=True, nullable=False)
    field_label = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), nullable=False, default="textarea")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)


class VerificationReport(db.Model):
    __tablename__ = "verification_reports"

    id = db.Column(db.Integer, primary_key=True)
    store_number = db.Column(db.String(10), nullable=False)
    report_date = db.Column(db.Date, nullable=False, default=today_et)

    supervisor_name = db.Column(db.String(120), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    values = db.relationship(
        "VerificationReportValue",
        backref="report",
        lazy=True,
        cascade="all, delete-orphan"
    )


class VerificationReportValue(db.Model):
    __tablename__ = "verification_report_values"

    id = db.Column(db.Integer, primary_key=True)

    report_id = db.Column(db.Integer, db.ForeignKey("verification_reports.id"), nullable=False)
    template_field_id = db.Column(db.Integer, db.ForeignKey("verification_template_fields.id"), nullable=False)

    field_key = db.Column(db.String(100), nullable=False)
    field_label = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    value_text = db.Column(db.Text, nullable=True)

    template_field = db.relationship("VerificationTemplateField")


# =========================
# PREP MODULE
# =========================

class PrepTemplateItem(db.Model):
    __tablename__ = "prep_template_items"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)

    section_name = db.Column(db.String(120), nullable=False)
    item_name = db.Column(db.String(255), nullable=False)

    build_to = db.Column(db.String(255), nullable=True)
    instructions = db.Column(db.Text, nullable=True)

    # Optional day-specific build-to values.
    # If blank, the app falls back to build_to.
    monday_build_to = db.Column(db.String(255), nullable=True)
    tuesday_build_to = db.Column(db.String(255), nullable=True)
    wednesday_build_to = db.Column(db.String(255), nullable=True)
    thursday_build_to = db.Column(db.String(255), nullable=True)
    friday_build_to = db.Column(db.String(255), nullable=True)
    saturday_build_to = db.Column(db.String(255), nullable=True)
    sunday_build_to = db.Column(db.String(255), nullable=True)

    # Optional editable prep import/rule fields.
    # These support future ideal-usage uploads and item-specific prep conversions.
    # They are intentionally nullable so existing live prep items keep working.
    report_item_name = db.Column(db.String(255), nullable=True)
    prep_unit = db.Column(db.String(80), nullable=True)
    rounding_increment = db.Column(db.String(80), nullable=True)
    minimum_build = db.Column(db.String(80), nullable=True)
    buffer_percent = db.Column(db.String(80), nullable=True)
    prep_coverage_days = db.Column(db.Integer, nullable=True)
    conversion_notes = db.Column(db.Text, nullable=True)

    monday = db.Column(db.Boolean, default=True)
    tuesday = db.Column(db.Boolean, default=True)
    wednesday = db.Column(db.Boolean, default=True)
    thursday = db.Column(db.Boolean, default=True)
    friday = db.Column(db.Boolean, default=True)
    saturday = db.Column(db.Boolean, default=True)
    sunday = db.Column(db.Boolean, default=True)

    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)


class DailyPrep(db.Model):
    __tablename__ = "daily_preps"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)
    prep_date = db.Column(db.Date, nullable=False, default=today_et)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship(
        "DailyPrepItem",
        backref="daily_prep",
        lazy=True,
        cascade="all, delete-orphan"
    )


class DailyPrepItem(db.Model):
    __tablename__ = "daily_prep_items"

    id = db.Column(db.Integer, primary_key=True)

    daily_prep_id = db.Column(
        db.Integer,
        db.ForeignKey("daily_preps.id"),
        nullable=False
    )

    template_item_id = db.Column(
        db.Integer,
        db.ForeignKey("prep_template_items.id"),
        nullable=False
    )

    section_name = db.Column(db.String(120), nullable=False)
    item_name = db.Column(db.String(255), nullable=False)

    build_to = db.Column(db.String(255), nullable=True)
    instructions = db.Column(db.Text, nullable=True)

    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    template_item = db.relationship("PrepTemplateItem")