import json
import os
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, session, url_for

from app import db
from app.dwp import dwp_bp
from app.models import Store, User
from app.services.email_service import send_email


class HRFormRequest(db.Model):
    __tablename__ = "hr_form_requests"

    id = db.Column(db.Integer, primary_key=True)
    form_type = db.Column(db.String(40), nullable=False, index=True)
    submitter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    subject_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    store_number = db.Column(db.String(10), nullable=True, index=True)
    status = db.Column(db.String(50), nullable=False, default="submitted", index=True)
    approval_role = db.Column(db.String(30), nullable=True, index=True)
    data_json = db.Column(db.Text, nullable=False, default="{}")
    decision_comment = db.Column(db.Text, nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    subject_acknowledged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    submitter = db.relationship("User", foreign_keys=[submitter_id])
    subject_user = db.relationship("User", foreign_keys=[subject_user_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    @property
    def data(self):
        try:
            return json.loads(self.data_json or "{}")
        except (TypeError, ValueError):
            return {}


class HRFormEvent(db.Model):
    __tablename__ = "hr_form_events"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("hr_form_requests.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    actor = db.relationship("User", foreign_keys=[actor_id])
    form = db.relationship("HRFormRequest", backref=db.backref("events", lazy=True, cascade="all, delete-orphan"))


MANAGEMENT_ROLES = {"manager", "general_manager", "supervisor", "admin", "hr"}
HR_EMAIL = os.getenv("HR_FORMS_HR_EMAIL", "hr@bostonpie.com").strip()
PAYROLL_EMAIL = os.getenv("HR_FORMS_PAYROLL_EMAIL", "payroll@bostonpie.com").strip()


def _user():
    user_id = session.get("user_id")
    if not user_id:
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(403)
    return user


def _role(user=None):
    user = user or _user()
    return (getattr(user, "role", None) or session.get("account_role") or session.get("user_role") or "").strip().lower()


def _name(user):
    return getattr(user, "name", None) or getattr(user, "username", None) or getattr(user, "email", None) or f"User {user.id}"


def _email(user):
    if not user:
        return None
    try:
        return user.get_notification_email()
    except Exception:
        return getattr(user, "notification_email", None) or getattr(user, "email", None)


def _safe_email(to_email, subject, body):
    if not to_email:
        return False
    try:
        send_email(to_email=to_email, subject=subject, body=body)
        return True
    except Exception:
        return False


def _add_event(form, actor, action, note=None):
    db.session.add(HRFormEvent(form_id=form.id, actor_id=actor.id if actor else None, action=action, note=note))


def _active_users_for(user):
    query = User.query.filter_by(is_active=True)
    role = _role(user)
    if role in {"admin", "hr"}:
        return query.order_by(User.store_number.asc(), User.name.asc()).all()
    if role == "supervisor":
        stores = [row.store_number for row in Store.query.filter_by(area_name=user.area_name, is_active=True).all()]
        return query.filter(User.store_number.in_(stores)).order_by(User.store_number.asc(), User.name.asc()).all()
    return query.filter(User.store_number == user.store_number).order_by(User.name.asc()).all()


def _next_time_off_status(user):
    role = _role(user)
    if role == "tm":
        return "pending_manager", "manager"
    if role in {"manager", "general_manager"}:
        return "pending_supervisor", "supervisor"
    if role == "supervisor":
        return "pending_admin", "admin"
    if role == "admin":
        return "pending_hr", "hr"
    return "pending_admin", "admin"


def _approver_emails(form):
    if form.approval_role == "manager":
        rows = User.query.filter(User.is_active.is_(True), User.store_number == form.store_number, User.role.in_(["manager", "general_manager"])).all()
    elif form.approval_role == "supervisor":
        store = Store.query.filter_by(store_number=form.store_number).first()
        rows = User.query.filter(User.is_active.is_(True), User.role == "supervisor", User.area_name == (store.area_name if store else None)).all()
    elif form.approval_role == "hr":
        rows = User.query.filter(User.is_active.is_(True), User.role == "hr").all()
    else:
        rows = User.query.filter(User.is_active.is_(True), User.role == "admin").all()
    return [email for email in {_email(row) for row in rows} if email]


def _can_view(form, user):
    role = _role(user)
    if role in {"admin", "hr"} or form.submitter_id == user.id or form.subject_user_id == user.id:
        return True
    if role in {"manager", "general_manager"}:
        return str(form.store_number or "") == str(user.store_number or "")
    if role == "supervisor":
        store = Store.query.filter_by(store_number=form.store_number).first()
        return bool(store and user.area_name and store.area_name == user.area_name)
    return False


def _can_approve(form, user):
    role = _role(user)
    if form.status == "pending_manager":
        return role in {"manager", "general_manager", "admin", "hr"} and (role in {"admin", "hr"} or str(user.store_number) == str(form.store_number))
    if form.status == "pending_supervisor":
        if role in {"admin", "hr"}:
            return True
        if role != "supervisor":
            return False
        store = Store.query.filter_by(store_number=form.store_number).first()
        return bool(store and store.area_name == user.area_name)
    if form.status == "pending_admin":
        return role == "admin"
    if form.status == "pending_hr":
        return role == "hr"
    return False


def _notify_submission(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = f"""A new BPI Ops HR form requires attention.\n\nForm: {form.form_type.replace('_', ' ').title()}\nTeam Member: {_name(form.subject_user)}\nStore: {form.store_number or '—'}\nStatus: {form.status.replace('_', ' ').title()}\n\nOpen: {link}\n"""
    for email in _approver_emails(form):
        _safe_email(email, f"HR Form Approval Required - {_name(form.subject_user)}", body)

    if form.form_type == "pay_change":
        for email in {HR_EMAIL, PAYROLL_EMAIL, _email(form.subject_user)}:
            _safe_email(email, f"Position/Rate/Store Change - {_name(form.subject_user)}", body)


def _notify_decision(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = f"""A BPI Ops HR form has been {form.status}.\n\nForm: {form.form_type.replace('_', ' ').title()}\nTeam Member: {_name(form.subject_user)}\nStore: {form.store_number or '—'}\nDecision: {form.status.title()}\nComments: {form.decision_comment or 'None'}\n\nOpen: {link}\n"""
    recipients = {HR_EMAIL, PAYROLL_EMAIL, _email(form.subject_user), _email(form.submitter)}
    store = Store.query.filter_by(store_number=form.store_number).first()
    if store:
        supervisors = User.query.filter_by(role="supervisor", area_name=store.area_name, is_active=True).all()
        recipients.update(_email(row) for row in supervisors)
    for email in recipients:
        _safe_email(email, f"HR Form {form.status.title()} - {_name(form.subject_user)}", body)


@dwp_bp.route("/hr-forms")
def hr_forms_home():
    user = _user()
    role = _role(user)
    query = HRFormRequest.query
    if role not in {"admin", "hr"}:
        visible_ids = [row.id for row in query.all() if _can_view(row, user)]
        query = query.filter(HRFormRequest.id.in_(visible_ids or [-1]))
    forms = query.order_by(HRFormRequest.created_at.desc()).limit(200).all()
    approvals = [row for row in forms if _can_approve(row, user)]
    return render_template("hr_forms/index.html", forms=forms, approvals=approvals, user=user, can_pay_change=role in MANAGEMENT_ROLES)


@dwp_bp.route("/hr-forms/time-off/new", methods=["GET", "POST"])
def hr_time_off_new():
    user = _user()
    if request.method == "POST":
        dates = [value.strip() for value in request.form.getlist("requested_date") if value.strip()]
        hours = [value.strip() for value in request.form.getlist("requested_hours")]
        if not dates:
            flash("Enter at least one requested date.", "error")
            return redirect(url_for("dwp.hr_time_off_new"))
        rows = [{"date": day, "hours": hours[index] if index < len(hours) else ""} for index, day in enumerate(dates)]
        status, approval_role = _next_time_off_status(user)
        form = HRFormRequest(
            form_type="time_off",
            submitter_id=user.id,
            subject_user_id=user.id,
            store_number=user.store_number,
            status=status,
            approval_role=approval_role,
            data_json=json.dumps({
                "leave_type": request.form.get("leave_type", ""),
                "sick_reason": request.form.get("sick_reason", ""),
                "dates": rows,
                "employee_signature": request.form.get("employee_signature", "").strip(),
                "comments": request.form.get("comments", "").strip(),
            }),
        )
        if not form.data["employee_signature"]:
            flash("Type your name as your electronic signature.", "error")
            return redirect(url_for("dwp.hr_time_off_new"))
        db.session.add(form)
        db.session.flush()
        _add_event(form, user, "submitted", f"Routed to {approval_role}")
        db.session.commit()
        _notify_submission(form)
        flash("Time-off request submitted.", "success")
        return redirect(url_for("dwp.hr_form_detail", form_id=form.id))
    return render_template("hr_forms/time_off_new.html", user=user)


@dwp_bp.route("/hr-forms/pay-change/new", methods=["GET", "POST"])
def hr_pay_change_new():
    user = _user()
    if _role(user) not in MANAGEMENT_ROLES:
        abort(403)
    users = _active_users_for(user)
    if request.method == "POST":
        subject_id = request.form.get("subject_user_id", type=int)
        subject = next((row for row in users if row.id == subject_id), None)
        if not subject:
            flash("Choose a valid active team member.", "error")
            return redirect(url_for("dwp.hr_pay_change_new"))
        payload = {key: request.form.get(key, "").strip() for key in [
            "change_type", "effective_date", "from_position", "to_position", "from_rate", "to_rate",
            "from_store", "to_store", "personal_time_action", "reason", "comments", "cash_hourly_wage",
            "tip_credit", "manager_signature"
        ]}
        if not payload["change_type"] or not payload["effective_date"] or not payload["manager_signature"]:
            flash("Change type, effective date, and manager signature are required.", "error")
            return redirect(url_for("dwp.hr_pay_change_new"))
        form = HRFormRequest(
            form_type="pay_change",
            submitter_id=user.id,
            subject_user_id=subject.id,
            store_number=subject.store_number,
            status="pending_tm_acknowledgement",
            approval_role=None,
            data_json=json.dumps(payload),
        )
        db.session.add(form)
        db.session.flush()
        _add_event(form, user, "submitted", "Sent to team member, HR, and Payroll")
        db.session.commit()
        _notify_submission(form)
        flash("Position/rate/store change sent to the team member, HR, and Payroll.", "success")
        return redirect(url_for("dwp.hr_form_detail", form_id=form.id))
    return render_template("hr_forms/pay_change_new.html", user=user, users=users)


@dwp_bp.route("/hr-forms/<int:form_id>")
def hr_form_detail(form_id):
    user = _user()
    form = HRFormRequest.query.get_or_404(form_id)
    if not _can_view(form, user):
        abort(403)
    return render_template("hr_forms/detail.html", form=form, user=user, can_approve=_can_approve(form, user))


@dwp_bp.route("/hr-forms/<int:form_id>/decision", methods=["POST"])
def hr_form_decision(form_id):
    user = _user()
    form = HRFormRequest.query.get_or_404(form_id)
    if not _can_approve(form, user):
        abort(403)
    decision = request.form.get("decision", "").strip().lower()
    if decision not in {"approved", "denied"}:
        abort(400)
    form.status = decision
    form.decision_comment = request.form.get("decision_comment", "").strip()
    form.approved_by_id = user.id
    form.approved_at = datetime.utcnow()
    form.approval_role = None
    _add_event(form, user, decision, form.decision_comment)
    db.session.commit()
    _notify_decision(form)
    flash(f"Request {decision}.", "success")
    return redirect(url_for("dwp.hr_form_detail", form_id=form.id))


@dwp_bp.route("/hr-forms/<int:form_id>/acknowledge", methods=["POST"])
def hr_form_acknowledge(form_id):
    user = _user()
    form = HRFormRequest.query.get_or_404(form_id)
    if form.form_type != "pay_change" or form.subject_user_id != user.id or form.status != "pending_tm_acknowledgement":
        abort(403)
    typed_name = request.form.get("typed_name", "").strip()
    if not typed_name:
        flash("Type your name to acknowledge the form.", "error")
        return redirect(url_for("dwp.hr_form_detail", form_id=form.id))
    form.subject_acknowledged_at = datetime.utcnow()
    form.status = "completed"
    data = form.data
    data["team_member_signature"] = typed_name
    form.data_json = json.dumps(data)
    _add_event(form, user, "acknowledged", typed_name)
    db.session.commit()
    _notify_decision(form)
    flash("Form acknowledged and completed.", "success")
    return redirect(url_for("dwp.hr_form_detail", form_id=form.id))
