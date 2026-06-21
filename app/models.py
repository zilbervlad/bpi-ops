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
    position = db.Column(db.String(80), nullable=True)

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

    def is_hr(self):
        return self.role == "hr"

    def get_notification_email(self):
        if not self.email_enabled:
            return None
        return self.notification_email or self.email



class PendingRegistrationRequest(db.Model):
    __tablename__ = "pending_registration_requests"

    id = db.Column(db.Integer, primary_key=True)

    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(40), nullable=True)

    requested_position = db.Column(db.String(80), nullable=True)
    store_number = db.Column(db.String(10), nullable=True)

    password_hash = db.Column(db.String(255), nullable=False)

    status = db.Column(db.String(30), nullable=False, default="pending")
    review_notes = db.Column(db.Text, nullable=True)

    approved_role = db.Column(db.String(50), nullable=True)
    created_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_user = db.relationship("User", foreign_keys=[created_user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_user_id])



class ModuleAccessSetting(db.Model):
    __tablename__ = "module_access_settings"

    id = db.Column(db.Integer, primary_key=True)
    module_key = db.Column(db.String(80), nullable=False, unique=True)
    module_label = db.Column(db.String(120), nullable=False)
    module_group = db.Column(db.String(80), nullable=False, default="General")
    allowed_roles_json = db.Column(db.Text, nullable=False, default="[]")
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=100)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class HRDocument(db.Model):
    __tablename__ = "hr_documents"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=True)

    original_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    file_data = db.Column(db.LargeBinary, nullable=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    recipients = db.relationship("HRDocumentRecipient", back_populates="document", cascade="all, delete-orphan")


class HRDocumentRecipient(db.Model):
    __tablename__ = "hr_document_recipients"
    __table_args__ = (
        db.UniqueConstraint("document_id", "user_id", name="uq_hr_document_recipient_user"),
    )

    id = db.Column(db.Integer, primary_key=True)

    document_id = db.Column(db.Integer, db.ForeignKey("hr_documents.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(db.String(30), nullable=False, default="pending")
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    email_sent_at = db.Column(db.DateTime, nullable=True)
    email_error = db.Column(db.Text, nullable=True)

    acknowledged_at = db.Column(db.DateTime, nullable=True)
    acknowledged_name = db.Column(db.String(160), nullable=True)
    acknowledged_ip = db.Column(db.String(80), nullable=True)
    acknowledged_user_agent = db.Column(db.String(255), nullable=True)

    document = db.relationship("HRDocument", back_populates="recipients")
    user = db.relationship("User", foreign_keys=[user_id])



class HRDocumentEmailJob(db.Model):
    __tablename__ = "hr_document_email_jobs"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("hr_documents.id"), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    job_type = db.Column(db.String(30), nullable=False, default="reminder")
    status = db.Column(db.String(30), nullable=False, default="queued")  # queued, running, completed, failed

    total_unsigned = db.Column(db.Integer, nullable=False, default=0)
    total_sendable = db.Column(db.Integer, nullable=False, default=0)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    no_email_count = db.Column(db.Integer, nullable=False, default=0)
    processed_count = db.Column(db.Integer, nullable=False, default=0)

    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    document = db.relationship("HRDocument", backref="email_jobs")
    requested_by = db.relationship("User", foreign_keys=[requested_by_user_id])


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


class ChecklistAutoEmailSettings(db.Model):
    __tablename__ = "checklist_auto_email_settings"

    id = db.Column(db.Integer, primary_key=True)

    enabled = db.Column(db.Boolean, nullable=False, default=False)
    send_11am = db.Column(db.Boolean, nullable=False, default=True)
    send_4pm = db.Column(db.Boolean, nullable=False, default=True)

    send_store_emails = db.Column(db.Boolean, nullable=False, default=True)
    send_admin_summary = db.Column(db.Boolean, nullable=False, default=True)
    send_supervisor_summary = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChecklistAutoEmailLog(db.Model):
    __tablename__ = "checklist_auto_email_logs"

    id = db.Column(db.Integer, primary_key=True)

    summary_date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.String(20), nullable=False)  # 11am / 4pm
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    sent_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    triggered_by = db.Column(db.String(50), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("summary_date", "slot", name="uq_checklist_auto_email_date_slot"),
    )


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
    # Maintenance calendar scheduling fields
    assigned_to = db.Column(db.String(120), nullable=True)
    scheduled_date = db.Column(db.Date, nullable=True)
    scheduled_time = db.Column(db.Time, nullable=True)
    estimated_minutes = db.Column(db.Integer, nullable=True)
    priority = db.Column(db.String(30), nullable=False, default="normal")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    svr_report = db.relationship("SVRReport")


class MaintenanceTimeCard(db.Model):
    __tablename__ = "maintenance_time_cards"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False, default=today_et)

    clock_in_at = db.Column(db.DateTime, nullable=True)
    clock_out_at = db.Column(db.DateTime, nullable=True)

    notes = db.Column(db.Text, nullable=True)

    # True when a time card is manually added/edited outside normal clock in/out.
    is_edited = db.Column(db.Boolean, nullable=False, default=False)
    edited_at = db.Column(db.DateTime, nullable=True)
    edited_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    edited_by = db.relationship("User", foreign_keys=[edited_by_user_id])

    __table_args__ = (
        db.UniqueConstraint("user_id", "work_date", name="uq_maintenance_time_card_user_date"),
    )


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
# FORMS MODULE
# =========================

class FormTemplate(db.Model):
    __tablename__ = "form_templates"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # JSON arrays stored as text. Example: ["admin", "supervisor", "manager"]
    submit_roles_json = db.Column(db.Text, nullable=True)
    view_roles_json = db.Column(db.Text, nullable=True)

    # Workflow / routing settings
    notify_gm = db.Column(db.Boolean, nullable=False, default=True)
    notify_supervisor = db.Column(db.Boolean, nullable=False, default=True)
    notify_admin = db.Column(db.Boolean, nullable=False, default=True)
    notify_hr = db.Column(db.Boolean, nullable=False, default=False)
    notify_payroll = db.Column(db.Boolean, nullable=False, default=False)

    requires_gm_approval = db.Column(db.Boolean, nullable=False, default=False)
    requires_supervisor_approval = db.Column(db.Boolean, nullable=False, default=False)
    requires_hr_approval = db.Column(db.Boolean, nullable=False, default=False)
    requires_payroll_processing = db.Column(db.Boolean, nullable=False, default=False)
    notify_employee_when_complete = db.Column(db.Boolean, nullable=False, default=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User")

    questions = db.relationship(
        "FormQuestion",
        backref="template",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="FormQuestion.sort_order"
    )

    submissions = db.relationship(
        "FormSubmission",
        backref="template",
        lazy=True,
        cascade="all, delete-orphan"
    )


class FormQuestion(db.Model):
    __tablename__ = "form_questions"

    id = db.Column(db.Integer, primary_key=True)

    form_template_id = db.Column(
        db.Integer,
        db.ForeignKey("form_templates.id"),
        nullable=False
    )

    question_text = db.Column(db.String(255), nullable=False)

    # Supported: short_text, long_text, yes_no, number, date, dropdown
    field_type = db.Column(db.String(50), nullable=False, default="short_text")

    is_required = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # For dropdown fields, JSON array as text. Example: ["Open", "Closed"]
    options_json = db.Column(db.Text, nullable=True)

    # For scored forms. Yes/No questions with weight > 0 count toward score.
    weight = db.Column(db.Integer, nullable=False, default=0)
    is_critical = db.Column(db.Boolean, nullable=False, default=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)


class FormSubmission(db.Model):
    __tablename__ = "form_submissions"

    id = db.Column(db.Integer, primary_key=True)

    form_template_id = db.Column(
        db.Integer,
        db.ForeignKey("form_templates.id"),
        nullable=False
    )

    store_number = db.Column(db.String(10), nullable=False)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    workflow_status = db.Column(db.String(50), nullable=False, default="submitted")
    workflow_completed_at = db.Column(db.DateTime, nullable=True)
    workflow_notes = db.Column(db.Text, nullable=True)

    # Scoring fields are generic so any Yes/No form can become a scored form later.
    score_earned = db.Column(db.Integer, nullable=False, default=0)
    score_possible = db.Column(db.Integer, nullable=False, default=0)
    score_percent = db.Column(db.Float, nullable=False, default=0.0)
    grade = db.Column(db.String(10), nullable=True)

    failed_count = db.Column(db.Integer, nullable=False, default=0)
    critical_failed_count = db.Column(db.Integer, nullable=False, default=0)

    submitted_by = db.relationship("User")

    answers = db.relationship(
        "FormAnswer",
        backref="submission",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="FormAnswer.sort_order"
    )


class FormAnswer(db.Model):
    __tablename__ = "form_answers"

    id = db.Column(db.Integer, primary_key=True)

    form_submission_id = db.Column(
        db.Integer,
        db.ForeignKey("form_submissions.id"),
        nullable=False
    )

    form_question_id = db.Column(
        db.Integer,
        db.ForeignKey("form_questions.id"),
        nullable=False
    )

    question_text = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    answer_text = db.Column(db.Text, nullable=True)

    weight = db.Column(db.Integer, nullable=False, default=0)
    is_critical = db.Column(db.Boolean, nullable=False, default=False)

    # Useful for dashboards later.
    is_failure = db.Column(db.Boolean, nullable=False, default=False)
    is_critical_failure = db.Column(db.Boolean, nullable=False, default=False)

    question = db.relationship("FormQuestion")


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

class DWPRecord(db.Model):
    __tablename__ = "dwp_records"

    id = db.Column(db.Integer, primary_key=True)

    conversation_date = db.Column(db.Date, nullable=False)
    infraction_date = db.Column(db.Date, nullable=False)

    store_number = db.Column(db.String(10), nullable=False)

    team_member_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    team_member_name_snapshot = db.Column(db.String(150), nullable=False)

    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    submitted_by_name_snapshot = db.Column(db.String(150), nullable=False)

    discussion_type = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(80), nullable=False)

    previous_conversations = db.Column(db.Text, nullable=True)
    expected_performance = db.Column(db.Text, nullable=False)
    actual_performance = db.Column(db.Text, nullable=False)
    team_member_statement = db.Column(db.Text, nullable=True)
    business_reason = db.Column(db.Text, nullable=False)
    logical_consequence = db.Column(db.Text, nullable=False)
    team_member_agrees_to = db.Column(db.Text, nullable=False)
    additional_comments = db.Column(db.Text, nullable=True)

    letter_filename = db.Column(db.String(255), nullable=True)
    letter_original_filename = db.Column(db.String(255), nullable=True)
    letter_content_type = db.Column(db.String(120), nullable=True)
    letter_data = db.Column(db.LargeBinary, nullable=True)
    letter_uploaded_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(db.String(40), nullable=False, default="filed")

    acknowledged_at = db.Column(db.DateTime, nullable=True)
    acknowledged_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    acknowledged_name = db.Column(db.String(150), nullable=True)
    acknowledgement_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    team_member = db.relationship("User", foreign_keys=[team_member_id])
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    acknowledged_by = db.relationship("User", foreign_keys=[acknowledged_by_id])

    @property
    def requires_letter(self):
        return self.discussion_type in ["Written Reminder", "DML - Decision Making Leave"]
