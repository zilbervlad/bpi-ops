import json
import re
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.services.email_service import send_email
from app.models import FormAnswer, FormQuestion, FormSubmission, FormTemplate, Store, User, today_et


forms_bp = Blueprint("forms", __name__, url_prefix="/forms")


FIELD_TYPES = [
    ("short_text", "Short text"),
    ("long_text", "Long text"),
    ("yes_no", "Yes / No"),
    ("number", "Number"),
    ("date", "Date"),
    ("dropdown", "Dropdown"),
]



def truthy_template_flag(template, field_name, default=False):
    value = getattr(template, field_name, default)
    if value is None:
        return default
    return bool(value)


def initial_workflow_status(template):
    if truthy_template_flag(template, "requires_gm_approval"):
        return "pending_gm"
    if truthy_template_flag(template, "requires_supervisor_approval"):
        return "pending_supervisor"
    if truthy_template_flag(template, "requires_hr_approval"):
        return "pending_hr"
    if truthy_template_flag(template, "requires_payroll_processing"):
        return "pending_payroll"
    return "submitted"


def current_access_role():
    return session.get("user_role")


def current_account_role():
    return session.get("account_role", current_access_role())


def current_user_id():
    return session.get("user_id")


def require_login():
    return bool(session.get("user_id"))


def role_set():
    roles = set()
    if current_access_role():
        roles.add(current_access_role())
    if current_account_role():
        roles.add(current_account_role())
    return roles


def is_admin():
    return current_access_role() == "admin"


def can_manage_forms():
    return current_access_role() in {"admin", "hr", "supervisor"}


def load_roles(json_text, default_roles=None):
    if default_roles is None:
        default_roles = ["admin", "supervisor", "manager", "general_manager"]

    if not json_text:
        return default_roles

    try:
        parsed = json.loads(json_text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    return default_roles


def role_allowed(json_text):
    if is_admin():
        return True

    allowed = set(load_roles(json_text))
    return bool(role_set() & allowed)


def user_store_number():
    return session.get("user_store") or session.get("store_number")


def user_area_name():
    return session.get("user_area") or session.get("area_name")


def visible_store_numbers():
    role = current_access_role()

    if role == "admin":
        stores = Store.query.filter_by(is_active=True).all()
        return [store.store_number for store in stores]

    if role == "supervisor":
        area_name = user_area_name()
        if not area_name:
            return []
        stores = Store.query.filter_by(area_name=area_name, is_active=True).all()
        return [store.store_number for store in stores]

    store_number = user_store_number()
    return [store_number] if store_number else []


def store_choices():
    role = current_access_role()

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        area_name = user_area_name()
        if not area_name:
            return []
        return Store.query.filter_by(
            area_name=area_name,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    store_number = user_store_number()
    if store_number:
        return Store.query.filter_by(
            store_number=store_number,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def slugify(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "form"


def unique_slug(title, existing_template_id=None):
    base_slug = slugify(title)
    slug = base_slug
    counter = 2

    while True:
        query = FormTemplate.query.filter_by(slug=slug)
        if existing_template_id:
            query = query.filter(FormTemplate.id != existing_template_id)

        if not query.first():
            return slug

        slug = f"{base_slug}-{counter}"
        counter += 1


def parse_options(options_text):
    if not options_text:
        return None

    options = [line.strip() for line in options_text.splitlines() if line.strip()]
    if not options:
        return None

    return json.dumps(options)


def get_options(question):
    if not question.options_json:
        return []
    try:
        parsed = json.loads(question.options_json)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def grade_from_score(percent, critical_failed_count=0):
    if percent >= 95:
        return "A+"
    if percent >= 90:
        return "A"
    if percent >= 80:
        return "B"
    if percent >= 70:
        return "C"
    return "F"


def send_form_submission_email(submission: FormSubmission):
    template = submission.template

    manager_user = User.query.filter_by(
        store_number=submission.store_number,
        role="manager",
        is_active=True
    ).first()
    manager_email = manager_user.get_notification_email() if manager_user else None

    store = Store.query.filter_by(store_number=submission.store_number).first()

    supervisor = None
    if store:
        supervisor = User.query.filter_by(
            area_name=store.area_name,
            role="supervisor",
            is_active=True
        ).first()
    supervisor_email = supervisor.get_notification_email() if supervisor else None

    admin_emails = []
    if truthy_template_flag(template, "notify_admin", True):
        admin_users = User.query.filter_by(role="admin", is_active=True).all()
        for admin in admin_users:
            email = admin.get_notification_email()
            if email:
                admin_emails.append(email)

    hr_emails = []
    if truthy_template_flag(template, "notify_hr", False):
        hr_users = User.query.filter_by(role="hr", is_active=True).all()
        for hr_user in hr_users:
            email = hr_user.get_notification_email()
            if email:
                hr_emails.append(email)

    payroll_emails = []
    if truthy_template_flag(template, "notify_payroll", False):
        payroll_users = User.query.filter_by(role="payroll", is_active=True).all()
        for payroll_user in payroll_users:
            email = payroll_user.get_notification_email()
            if email:
                payroll_emails.append(email)

    recipients = []
    if truthy_template_flag(template, "notify_gm", True) and manager_email:
        recipients.append(manager_email)
    if truthy_template_flag(template, "notify_supervisor", True) and supervisor_email:
        recipients.append(supervisor_email)

    recipients.extend(admin_emails)
    recipients.extend(hr_emails)
    recipients.extend(payroll_emails)

    recipients = [email for email in dict.fromkeys(recipients) if email]

    if not recipients:
        raise ValueError("No email recipients configured for this form template.")

    to_email = recipients[0]
    cc_emails = recipients[1:]

    answers = (
        FormAnswer.query
        .filter_by(form_submission_id=submission.id)
        .order_by(FormAnswer.sort_order.asc(), FormAnswer.id.asc())
        .all()
    )

    failed_answers = [answer for answer in answers if answer.is_failure]
    submitted_by = submission.submitted_by.name if submission.submitted_by else "Unknown"

    failed_text = "None"
    if failed_answers:
        failed_text = "\n".join(
            f"- {answer.question_text}: {answer.answer_text}"
            for answer in failed_answers
        )

    score_text = "Not scored"
    if submission.score_possible and submission.score_possible > 0:
        score_text = f"{submission.score_percent}% - {submission.grade}"

    body = (
        f"{template.title}\n"
        f"Store: {submission.store_number}\n"
        f"Submitted By: {submitted_by}\n"
        f"Submitted At: {submission.submitted_at.strftime('%B %d, %Y %I:%M %p')}\n"
        f"Workflow Status: {submission.workflow_status.replace('_', ' ').title()}\n\n"
        f"Score: {score_text}\n"
        f"Failed Items: {submission.failed_count}\n"
        f"Critical Failures: {submission.critical_failed_count}\n\n"
        f"Failed Item Details:\n"
        f"{failed_text}\n\n"
        f"- BPI Ops"
    )

    send_email(
        to_email=to_email,
        subject=f"Store {submission.store_number} {template.title}",
        body=body,
        cc_emails=cc_emails if cc_emails else None
    )

    return {
        "to_email": to_email,
        "manager_email": manager_email,
        "supervisor_email": supervisor_email,
        "admin_emails": admin_emails,
        "hr_emails": hr_emails,
        "payroll_emails": payroll_emails,
        "cc_emails": cc_emails,
        "recipients": recipients,
    }



WORKFLOW_STATUS_LABELS = {
    "submitted": "Submitted",
    "pending_gm": "Pending GM",
    "pending_supervisor": "Pending Supervisor",
    "pending_hr": "Pending HR",
    "pending_payroll": "Pending Payroll",
    "complete": "Complete",
    "rejected": "Rejected",
    "sent_back": "Sent Back",
}


def workflow_status_label(status):
    return WORKFLOW_STATUS_LABELS.get(status or "submitted", (status or "submitted").replace("_", " ").title())


def next_workflow_status(template, current_status):
    steps = []

    if truthy_template_flag(template, "requires_gm_approval"):
        steps.append("pending_gm")
    if truthy_template_flag(template, "requires_supervisor_approval"):
        steps.append("pending_supervisor")
    if truthy_template_flag(template, "requires_hr_approval"):
        steps.append("pending_hr")
    if truthy_template_flag(template, "requires_payroll_processing"):
        steps.append("pending_payroll")

    if not steps:
        return "complete"

    if current_status in ["submitted", None, ""]:
        return steps[0]

    if current_status not in steps:
        return "complete"

    index = steps.index(current_status)
    if index + 1 < len(steps):
        return steps[index + 1]

    return "complete"


def store_area_for_submission(submission):
    store = Store.query.filter_by(store_number=submission.store_number).first()
    return store.area_name if store else None


def can_act_on_workflow_submission(submission):
    role = current_access_role()

    if role == "admin":
        return True

    status = submission.workflow_status or "submitted"

    if status == "pending_gm":
        return role in {"general_manager", "manager"} and submission.store_number == user_store_number()

    if status == "pending_supervisor":
        return role == "supervisor" and store_area_for_submission(submission) == user_area_name()

    if status == "pending_hr":
        return role == "hr"

    if status == "pending_payroll":
        return role == "payroll"

    return False


def visible_workflow_query():
    query = FormSubmission.query.join(FormTemplate)

    if not is_admin():
        role = current_access_role()

        if role in {"manager", "general_manager"}:
            store_number = user_store_number()
            query = query.filter(FormSubmission.store_number == (store_number or "__none__"))

        elif role == "supervisor":
            allowed_stores = visible_store_numbers()
            query = query.filter(FormSubmission.store_number.in_(allowed_stores or ["__none__"]))

        elif role == "hr":
            query = query.filter(FormSubmission.workflow_status == "pending_hr")

        elif role == "payroll":
            query = query.filter(FormSubmission.workflow_status == "pending_payroll")

        else:
            query = query.filter(FormSubmission.id == -1)

    return query


def accessible_template_or_redirect(template_id, submit=False):
    template = FormTemplate.query.get_or_404(template_id)

    if submit and not template.is_active:
        flash("That form is not active right now.", "error")
        return None

    allowed = role_allowed(template.submit_roles_json if submit else template.view_roles_json)
    if not allowed:
        flash("You do not have access to that form.", "error")
        return None

    return template


@forms_bp.before_request
def require_user():
    if not require_login():
        flash("Please log in first.", "error")
        return redirect(url_for("auth.login"))


@forms_bp.route("/")
def index():
    templates = (
        FormTemplate.query
        .filter_by(is_active=True)
        .order_by(FormTemplate.title.asc())
        .all()
    )
    templates = [template for template in templates if role_allowed(template.submit_roles_json)]

    recent_query = (
        FormSubmission.query
        .join(FormTemplate)
        .order_by(FormSubmission.submitted_at.desc())
    )

    if not is_admin():
        allowed_stores = visible_store_numbers()
        if allowed_stores:
            recent_query = recent_query.filter(FormSubmission.store_number.in_(allowed_stores))
        else:
            recent_query = recent_query.filter(FormSubmission.store_number == "__none__")

    recent_submissions = recent_query.limit(8).all()

    return render_template(
        "forms/index.html",
        templates=templates,
        recent_submissions=recent_submissions,
    )


@forms_bp.route("/submit/<int:template_id>", methods=["GET", "POST"])
def submit_form(template_id):
    template = accessible_template_or_redirect(template_id, submit=True)
    if template is None:
        return redirect(url_for("forms.index"))

    questions = (
        FormQuestion.query
        .filter_by(form_template_id=template.id, is_active=True)
        .order_by(FormQuestion.sort_order.asc(), FormQuestion.id.asc())
        .all()
    )

    stores = store_choices()
    locked_store = user_store_number() if current_access_role() in ["manager", "general_manager"] and user_store_number() else None

    if request.method == "POST":
        store_number = locked_store or request.form.get("store_number", "").strip()

        if not store_number:
            flash("Please choose a store.", "error")
            return redirect(url_for("forms.submit_form", template_id=template.id))

        errors = []
        answers_payload = []

        score_earned = 0
        score_possible = 0
        failed_count = 0
        critical_failed_count = 0

        for question in questions:
            field_name = f"question_{question.id}"
            value = request.form.get(field_name, "").strip()

            if question.is_required and not value:
                errors.append(question.question_text)

            is_failure = False
            is_critical_failure = False

            if question.field_type == "yes_no" and question.weight > 0:
                score_possible += question.weight
                if value.lower() == "yes":
                    score_earned += question.weight
                elif value.lower() == "no":
                    failed_count += 1
                    is_failure = True
                    if question.is_critical:
                        critical_failed_count += 1
                        is_critical_failure = True

            answers_payload.append(
                {
                    "question": question,
                    "value": value,
                    "is_failure": is_failure,
                    "is_critical_failure": is_critical_failure,
                }
            )

        if errors:
            flash("Please answer all required questions.", "error")
            return render_template(
                "forms/submit.html",
                template=template,
                questions=questions,
                stores=stores,
                locked_store=locked_store,
                get_options=get_options,
                today=today_et(),
            )

        score_percent = round((score_earned / score_possible) * 100, 1) if score_possible else 0.0
        grade = grade_from_score(score_percent, critical_failed_count)

        submission = FormSubmission(
            form_template_id=template.id,
            store_number=store_number,
            submitted_by_user_id=current_user_id(),
            submitted_at=datetime.utcnow(),
            score_earned=score_earned,
            score_possible=score_possible,
            score_percent=score_percent,
            grade=grade,
            failed_count=failed_count,
            critical_failed_count=critical_failed_count,
            workflow_status=initial_workflow_status(template),
        )
        db.session.add(submission)
        db.session.flush()

        for payload in answers_payload:
            question = payload["question"]
            db.session.add(
                FormAnswer(
                    form_submission_id=submission.id,
                    form_question_id=question.id,
                    question_text=question.question_text,
                    field_type=question.field_type,
                    sort_order=question.sort_order,
                    answer_text=payload["value"],
                    weight=question.weight,
                    is_critical=question.is_critical,
                    is_failure=payload["is_failure"],
                    is_critical_failure=payload["is_critical_failure"],
                )
            )

        db.session.commit()

        try:
            email_result = send_form_submission_email(submission)
            flash(
                f"Form submitted and emailed to {email_result['to_email']}.",
                "success"
            )
        except Exception as e:
            flash(f"Form submitted, but email failed: {str(e)}", "error")

        return redirect(url_for("forms.submission_detail", submission_id=submission.id))

    return render_template(
        "forms/submit.html",
        template=template,
        questions=questions,
        stores=stores,
        locked_store=locked_store,
        get_options=get_options,
        today=today_et(),
    )



@forms_bp.route("/workflow")
def workflow_inbox():
    selected_status = request.args.get("status", "").strip()

    status_order = [
        "pending_gm",
        "pending_supervisor",
        "pending_hr",
        "pending_payroll",
        "submitted",
        "sent_back",
        "rejected",
        "complete",
    ]

    query = visible_workflow_query()

    if selected_status:
        query = query.filter(FormSubmission.workflow_status == selected_status)
    else:
        query = query.filter(FormSubmission.workflow_status.in_([
            "pending_gm",
            "pending_supervisor",
            "pending_hr",
            "pending_payroll",
            "submitted",
            "sent_back",
        ]))

    submissions = (
        query
        .order_by(FormSubmission.submitted_at.desc())
        .limit(250)
        .all()
    )

    status_counts = {}
    count_query = visible_workflow_query()
    for status in status_order:
        status_counts[status] = count_query.filter(FormSubmission.workflow_status == status).count()

    return render_template(
        "forms/workflow.html",
        submissions=submissions,
        selected_status=selected_status,
        status_order=status_order,
        status_counts=status_counts,
        workflow_status_label=workflow_status_label,
        can_act_on_workflow_submission=can_act_on_workflow_submission,
    )


@forms_bp.route("/submissions/<int:submission_id>/workflow-action", methods=["POST"])
def workflow_action(submission_id):
    submission = FormSubmission.query.get_or_404(submission_id)

    if not can_act_on_workflow_submission(submission):
        flash("You do not have permission to update this workflow item.", "error")
        return redirect(url_for("forms.submission_detail", submission_id=submission.id))

    action = request.form.get("action", "").strip()
    note = request.form.get("workflow_notes", "").strip()

    if action == "approve":
        submission.workflow_status = next_workflow_status(submission.template, submission.workflow_status)

        if submission.workflow_status == "complete":
            submission.workflow_completed_at = datetime.utcnow()

        flash("Workflow item approved.", "success")

    elif action == "mark_processed":
        if submission.workflow_status != "pending_payroll":
            flash("Only payroll items can be marked processed.", "error")
            return redirect(url_for("forms.submission_detail", submission_id=submission.id))

        submission.workflow_status = "complete"
        submission.workflow_completed_at = datetime.utcnow()
        flash("Payroll item marked processed.", "success")

    elif action == "reject":
        submission.workflow_status = "rejected"
        submission.workflow_completed_at = datetime.utcnow()
        flash("Workflow item rejected.", "success")

    elif action == "send_back":
        submission.workflow_status = "sent_back"
        flash("Workflow item sent back.", "success")

    else:
        flash("Unknown workflow action.", "error")
        return redirect(url_for("forms.submission_detail", submission_id=submission.id))

    if note:
        existing_notes = submission.workflow_notes or ""
        timestamp = datetime.utcnow().strftime("%m/%d/%Y %I:%M %p")
        actor = session.get("user_name") or "Unknown"
        line = f"[{timestamp}] {actor}: {note}"
        submission.workflow_notes = (existing_notes + "\n" + line).strip() if existing_notes else line

    db.session.commit()

    return redirect(url_for("forms.workflow_inbox"))

@forms_bp.route("/submissions")
def submissions():
    template_id = request.args.get("template_id", type=int)
    store_number = request.args.get("store_number", "").strip()

    query = FormSubmission.query.join(FormTemplate)

    if template_id:
        query = query.filter(FormSubmission.form_template_id == template_id)

    if store_number:
        query = query.filter(FormSubmission.store_number == store_number)

    if not is_admin():
        allowed_stores = visible_store_numbers()
        if allowed_stores:
            query = query.filter(FormSubmission.store_number.in_(allowed_stores))
        else:
            query = query.filter(FormSubmission.store_number == "__none__")

    submissions = query.order_by(FormSubmission.submitted_at.desc()).limit(200).all()

    templates = FormTemplate.query.order_by(FormTemplate.title.asc()).all()
    stores = store_choices()

    return render_template(
        "forms/submissions.html",
        submissions=submissions,
        templates=templates,
        stores=stores,
        selected_template_id=template_id,
        selected_store_number=store_number,
    )


@forms_bp.route("/submissions/<int:submission_id>")
def submission_detail(submission_id):
    submission = FormSubmission.query.get_or_404(submission_id)

    if not role_allowed(submission.template.view_roles_json):
        flash("You do not have access to that submission.", "error")
        return redirect(url_for("forms.index"))

    if not is_admin():
        allowed_stores = visible_store_numbers()
        if submission.store_number not in allowed_stores:
            flash("You do not have access to that store submission.", "error")
            return redirect(url_for("forms.index"))

    return render_template("forms/detail.html", submission=submission)


@forms_bp.route("/admin", methods=["GET", "POST"])
def admin():
    if not can_manage_forms():
        flash("Forms Admin is available to Admin, HR, and Supervisors only.", "error")
        return redirect(url_for("forms.index"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()

        if not title:
            flash("Form title is required.", "error")
            return redirect(url_for("forms.admin"))

        template = FormTemplate(
            title=title,
            slug=unique_slug(title),
            description=description,
            is_active=True,
            submit_roles_json=json.dumps(request.form.getlist("submit_roles")),
            view_roles_json=json.dumps(request.form.getlist("view_roles")),
            notify_gm=bool(request.form.get("notify_gm")),
            notify_supervisor=bool(request.form.get("notify_supervisor")),
            notify_admin=bool(request.form.get("notify_admin")),
            notify_hr=bool(request.form.get("notify_hr")),
            notify_payroll=bool(request.form.get("notify_payroll")),
            requires_gm_approval=bool(request.form.get("requires_gm_approval")),
            requires_supervisor_approval=bool(request.form.get("requires_supervisor_approval")),
            requires_hr_approval=bool(request.form.get("requires_hr_approval")),
            requires_payroll_processing=bool(request.form.get("requires_payroll_processing")),
            notify_employee_when_complete=bool(request.form.get("notify_employee_when_complete")),
            created_by_user_id=current_user_id(),
        )
        db.session.add(template)
        db.session.commit()

        flash("Form created. Add questions next.", "success")
        return redirect(url_for("forms.edit_template", template_id=template.id))

    templates = FormTemplate.query.order_by(FormTemplate.title.asc()).all()

    return render_template(
        "forms/admin.html",
        templates=templates,
        role_options=["admin", "supervisor", "general_manager", "manager", "tm", "payroll"],
        load_roles=load_roles,
    )


@forms_bp.route("/admin/<int:template_id>", methods=["GET", "POST"])
def edit_template(template_id):
    if not can_manage_forms():
        flash("Forms Admin is available to Admin, HR, and Supervisors only.", "error")
        return redirect(url_for("forms.index"))

    template = FormTemplate.query.get_or_404(template_id)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_template":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()

            if not title:
                flash("Form title is required.", "error")
                return redirect(url_for("forms.edit_template", template_id=template.id))

            template.title = title
            template.slug = unique_slug(title, existing_template_id=template.id)
            template.description = description
            template.is_active = bool(request.form.get("is_active"))
            template.submit_roles_json = json.dumps(request.form.getlist("submit_roles"))
            template.view_roles_json = json.dumps(request.form.getlist("view_roles"))
            template.notify_gm = bool(request.form.get("notify_gm"))
            template.notify_supervisor = bool(request.form.get("notify_supervisor"))
            template.notify_admin = bool(request.form.get("notify_admin"))
            template.notify_hr = bool(request.form.get("notify_hr"))
            template.notify_payroll = bool(request.form.get("notify_payroll"))
            template.requires_gm_approval = bool(request.form.get("requires_gm_approval"))
            template.requires_supervisor_approval = bool(request.form.get("requires_supervisor_approval"))
            template.requires_hr_approval = bool(request.form.get("requires_hr_approval"))
            template.requires_payroll_processing = bool(request.form.get("requires_payroll_processing"))
            template.notify_employee_when_complete = bool(request.form.get("notify_employee_when_complete"))
            db.session.commit()

            flash("Form settings saved.", "success")
            return redirect(url_for("forms.edit_template", template_id=template.id))

        if action == "add_question":
            question_text = request.form.get("question_text", "").strip()
            field_type = request.form.get("field_type", "short_text").strip()
            is_required = bool(request.form.get("is_required"))
            is_critical = bool(request.form.get("is_critical"))
            weight = request.form.get("weight", type=int) or 0
            options_json = parse_options(request.form.get("options_text", ""))

            if not question_text:
                flash("Question text is required.", "error")
                return redirect(url_for("forms.edit_template", template_id=template.id))

            max_sort = max([q.sort_order for q in template.questions] or [0])

            db.session.add(
                FormQuestion(
                    form_template_id=template.id,
                    question_text=question_text,
                    field_type=field_type,
                    is_required=is_required,
                    sort_order=max_sort + 1,
                    options_json=options_json,
                    weight=weight,
                    is_critical=is_critical,
                    is_active=True,
                )
            )
            db.session.commit()

            flash("Question added.", "success")
            return redirect(url_for("forms.edit_template", template_id=template.id))

        if action == "update_questions":
            for question in template.questions:
                question.question_text = request.form.get(f"text_{question.id}", question.question_text).strip()
                question.field_type = request.form.get(f"type_{question.id}", question.field_type)
                question.is_required = bool(request.form.get(f"required_{question.id}"))
                question.is_critical = bool(request.form.get(f"critical_{question.id}"))
                question.is_active = bool(request.form.get(f"active_{question.id}"))
                question.weight = request.form.get(f"weight_{question.id}", type=int) or 0
                question.sort_order = request.form.get(f"sort_{question.id}", type=int) or question.sort_order
            db.session.commit()

            flash("Questions saved.", "success")
            return redirect(url_for("forms.edit_template", template_id=template.id))

    return render_template(
        "forms/edit_template.html",
        template=template,
        role_options=["admin", "supervisor", "general_manager", "manager", "tm", "payroll"],
        field_types=FIELD_TYPES,
        load_roles=load_roles,
    )
