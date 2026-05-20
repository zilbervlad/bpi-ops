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
    return current_access_role() == "admin"


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
    return session.get("store_number")


def store_choices():
    return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()


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


def get_answer_value(answers, search_text):
    search_text = search_text.lower()
    for answer in answers:
        if search_text in (answer.question_text or "").lower():
            return answer.answer_text or ""
    return ""


def collect_form_email_recipients(submission):
    """
    Sends form notifications to:
    - active admins
    - active supervisors
    - active general managers/managers assigned to that store

    Uses notification_email first, then email.
    """
    recipients = []

    users = (
        User.query
        .filter(User.is_active == True)
        .filter(User.email_enabled == True)
        .filter(
            (User.role.in_(["admin", "supervisor"])) |
            (
                (User.store_number == submission.store_number) &
                (User.role.in_(["general_manager", "manager"]))
            )
        )
        .all()
    )

    for user in users:
        email = user.get_notification_email()
        if email and email not in recipients:
            recipients.append(email)

    return recipients


def build_form_submission_email_body(submission):
    answers = (
        FormAnswer.query
        .filter_by(form_submission_id=submission.id)
        .order_by(FormAnswer.sort_order.asc(), FormAnswer.id.asc())
        .all()
    )

    failed_answers = [answer for answer in answers if answer.is_failure]
    critical_answers = [answer for answer in answers if answer.is_critical_failure]

    submitted_by = submission.submitted_by.name if submission.submitted_by else "Unknown"
    who_closed = get_answer_value(answers, "who closed")

    lines = [
        f"{submission.template.title} submitted",
        "",
        f"Store: {submission.store_number}",
        f"Submitted by: {submitted_by}",
        f"Submitted at: {submission.submitted_at.strftime('%m/%d/%Y %I:%M %p')}",
    ]

    if who_closed:
        lines.append(f"Who closed: {who_closed}")

    if submission.score_possible and submission.score_possible > 0:
        lines.extend([
            "",
            f"Score: {submission.score_percent}% - {submission.grade}",
            f"Failed Items: {submission.failed_count}",
            f"Critical Failures: {submission.critical_failed_count}",
        ])

    if failed_answers:
        lines.extend(["", "Failed Items:"])
        for answer in failed_answers:
            critical_label = " [CRITICAL]" if answer.is_critical_failure else ""
            lines.append(f"- {answer.question_text}: {answer.answer_text}{critical_label}")

    lines.extend(["", "All Answers:"])
    for answer in answers:
        lines.append(f"- {answer.question_text}: {answer.answer_text or '-'}")

    return "\n".join(lines)


def notify_form_submission(submission):
    recipients = collect_form_email_recipients(submission)

    if not recipients:
        print(f"No form email recipients found for submission {submission.id}.")
        return

    subject = f"{submission.template.title} Submitted - Store {submission.store_number}"
    if submission.score_possible and submission.score_possible > 0:
        subject += f" - {submission.score_percent}% {submission.grade}"

    body = build_form_submission_email_body(submission)

    try:
        send_email(
            to_email=recipients[0],
            subject=subject,
            body=body,
            cc_emails=recipients[1:] if len(recipients) > 1 else None,
        )
        print(f"Form submission email sent for submission {submission.id}.")
    except Exception as exc:
        # Do not block store operations if email fails.
        print(f"Form submission email failed for submission {submission.id}: {exc}")


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

    if not is_admin() and current_access_role() not in ["supervisor"]:
        if user_store_number():
            recent_query = recent_query.filter(FormSubmission.store_number == user_store_number())

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
    locked_store = user_store_number() if current_access_role() == "manager" and user_store_number() else None

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

        notify_form_submission(submission)

        flash("Form submitted.", "success")
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


@forms_bp.route("/submissions")
def submissions():
    template_id = request.args.get("template_id", type=int)
    store_number = request.args.get("store_number", "").strip()

    query = FormSubmission.query.join(FormTemplate)

    if template_id:
        query = query.filter(FormSubmission.form_template_id == template_id)

    if store_number:
        query = query.filter(FormSubmission.store_number == store_number)

    if not is_admin() and current_access_role() not in ["supervisor"]:
        if user_store_number():
            query = query.filter(FormSubmission.store_number == user_store_number())

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

    if not is_admin() and current_access_role() not in ["supervisor"]:
        if user_store_number() and submission.store_number != user_store_number():
            flash("You do not have access to that store submission.", "error")
            return redirect(url_for("forms.index"))

    return render_template("forms/detail.html", submission=submission)


@forms_bp.route("/admin", methods=["GET", "POST"])
def admin():
    if not can_manage_forms():
        flash("Forms Admin is admin-only.", "error")
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
        role_options=["admin", "supervisor", "general_manager", "manager", "tm"],
        load_roles=load_roles,
    )


@forms_bp.route("/admin/<int:template_id>", methods=["GET", "POST"])
def edit_template(template_id):
    if not can_manage_forms():
        flash("Forms Admin is admin-only.", "error")
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
        role_options=["admin", "supervisor", "general_manager", "manager", "tm"],
        field_types=FIELD_TYPES,
        load_roles=load_roles,
    )
