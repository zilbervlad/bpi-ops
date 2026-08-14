import json
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

from app import db
from app.dwp import dwp_bp
from app.dwp import hr_forms
from app.dwp import hr_forms_pdf_email
from app.dwp.hr_forms import HRFormRequest
from app.models import Store, User


VOLUNTARY_REASONS = [
    "Without notice or reason",
    "Another Job",
    "Transfer",
    "Illness",
    "Working Conditions",
    "Work Schedule",
    "Moving",
    "Other",
    "Problem with Supervisor",
    "Problem with Co-worker",
    "Personal",
    "Return to School",
    "Retirement",
    "Refused Suitable Work",
    "LOA - Did not Return",
    "Pay",
]

INVOLUNTARY_REASONS = [
    "Absenteeism",
    "Insubordination",
    "Violation of Work Rules",
    "Lack of Work",
    "Other",
    "Tardiness",
    "Unsatisfactory Performance",
    "Refusal to Follow Instruction",
    "Job Eliminated or Changed",
    "Disability",
]


def _current_user_or_none():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _separation_users_for(user):
    """Return in-scope employees, including inactive rows for post-separation paperwork."""
    role = hr_forms._role(user)
    query = User.query

    if role in {"admin", "hr"}:
        return (
            query
            .order_by(User.is_active.desc(), User.store_number.asc(), User.name.asc())
            .all()
        )

    if role == "supervisor":
        stores = [
            row.store_number
            for row in Store.query.filter_by(area_name=user.area_name).all()
        ]
        return (
            query
            .filter(User.store_number.in_(stores or ["__none__"]))
            .order_by(User.is_active.desc(), User.store_number.asc(), User.name.asc())
            .all()
        )

    return (
        query
        .filter(User.store_number == user.store_number)
        .order_by(User.is_active.desc(), User.name.asc())
        .all()
    )


@dwp_bp.context_processor
def inject_separation_notice_permissions():
    user = _current_user_or_none()
    if not user:
        return {"can_submit_separation": False}
    return {
        "can_submit_separation": hr_forms._role(user) in hr_forms.MANAGEMENT_ROLES,
    }


def _notify_separation(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        "A completed Employee Separation Notice has been submitted in BPI Ops.\n\n"
        f"Employee: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '-'}\n"
        f"Last Day Worked: {form.data.get('last_day_worked') or '-'}\n"
        f"Separation: {str(form.data.get('separation_type') or '').title()} - {form.data.get('separation_reason') or '-'}\n"
        f"Submitted By: {hr_forms._name(form.submitter)}\n\n"
        "The completed separation notice PDF is attached.\n\n"
        f"Open in BPI Ops: {link}\n"
    )

    recipients = {
        hr_forms.HR_EMAIL,
        hr_forms.PAYROLL_EMAIL,
        hr_forms._email(form.submitter),
    }

    admins = User.query.filter(
        User.is_active.is_(True),
        User.role == "admin",
    ).all()
    recipients.update(hr_forms._email(row) for row in admins)

    store = Store.query.filter_by(store_number=form.store_number).first()
    if store and store.area_name:
        supervisors = User.query.filter_by(
            role="supervisor",
            area_name=store.area_name,
            is_active=True,
        ).all()
        recipients.update(hr_forms._email(row) for row in supervisors)

    subject = f"Employee Separation Notice - {hr_forms._name(form.subject_user)} - Store {form.store_number or '-'}"
    for email in {email for email in recipients if email}:
        hr_forms_pdf_email._send(email, subject, body, form)


@dwp_bp.route("/hr-forms/separation/new", methods=["GET", "POST"])
def hr_separation_new():
    user = hr_forms._user()
    if hr_forms._role(user) not in hr_forms.MANAGEMENT_ROLES:
        abort(403)

    users = _separation_users_for(user)
    today = datetime.utcnow().date().isoformat()

    if request.method == "POST":
        subject_id = request.form.get("subject_user_id", type=int)
        subject = next((row for row in users if row.id == subject_id), None)
        if not subject:
            flash("Choose a valid team member in your store/area.", "error")
            return redirect(url_for("dwp.hr_separation_new"))

        separation_type = request.form.get("separation_type", "").strip().lower()
        separation_reason = request.form.get("separation_reason", "").strip()
        valid_reasons = (
            VOLUNTARY_REASONS
            if separation_type == "voluntary"
            else INVOLUNTARY_REASONS
            if separation_type == "involuntary"
            else []
        )
        if separation_reason not in valid_reasons:
            flash("Choose a valid reason for separation.", "error")
            return redirect(url_for("dwp.hr_separation_new"))

        payload = {
            "company": "BOSTON PIE INC.",
            "manager_name": hr_forms._name(user),
            "last_day_worked": request.form.get("last_day_worked", "").strip(),
            "separation_type": separation_type,
            "separation_reason": separation_reason,
            "transfer_date": request.form.get("transfer_date", "").strip(),
            "transfer_store": request.form.get("transfer_store", "").strip(),
            "explanation": request.form.get("explanation", "").strip(),
            "employee_statement": request.form.get("employee_statement", "").strip(),
            "notice_given": request.form.get("notice_given", "").strip().lower(),
            "notice_to_whom": request.form.get("notice_to_whom", "").strip(),
            "eligible_for_rehire": request.form.get("eligible_for_rehire", "").strip().lower(),
            "rehire_explanation": request.form.get("rehire_explanation", "").strip(),
            "employee_signature": request.form.get("employee_signature", "").strip(),
            "employee_signature_date": request.form.get("employee_signature_date", "").strip(),
            "manager_signature": request.form.get("manager_signature", "").strip(),
            "manager_signature_date": request.form.get("manager_signature_date", "").strip() or today,
        }

        if not payload["last_day_worked"]:
            flash("Last day worked is required.", "error")
            return redirect(url_for("dwp.hr_separation_new"))
        if not payload["explanation"]:
            flash("A detailed explanation for the separation is required.", "error")
            return redirect(url_for("dwp.hr_separation_new"))
        if payload["notice_given"] not in {"yes", "no"}:
            flash("Indicate whether the employee gave notice.", "error")
            return redirect(url_for("dwp.hr_separation_new"))
        if payload["eligible_for_rehire"] not in {"yes", "no"}:
            flash("Indicate whether the employee is eligible for rehire.", "error")
            return redirect(url_for("dwp.hr_separation_new"))
        if payload["eligible_for_rehire"] == "no" and not payload["rehire_explanation"]:
            flash("Explain why the employee is not eligible for rehire.", "error")
            return redirect(url_for("dwp.hr_separation_new"))
        if not payload["manager_signature"]:
            flash("Manager signature is required.", "error")
            return redirect(url_for("dwp.hr_separation_new"))

        form = HRFormRequest(
            form_type="separation_notice",
            submitter_id=user.id,
            subject_user_id=subject.id,
            store_number=subject.store_number,
            status="completed",
            approval_role=None,
            data_json=json.dumps(payload),
        )
        db.session.add(form)
        db.session.flush()
        hr_forms._add_event(form, user, "submitted", "Completed separation notice sent to HR and Payroll")
        db.session.commit()

        _notify_separation(form)
        flash("Employee Separation Notice submitted to HR and Payroll.", "success")
        return redirect(url_for("dwp.hr_form_detail", form_id=form.id))

    return render_template(
        "hr_forms/separation_new.html",
        user=user,
        users=users,
        voluntary_reasons=VOLUNTARY_REASONS,
        involuntary_reasons=INVOLUNTARY_REASONS,
        today=today,
    )


@dwp_bp.before_request
def render_separation_notice_detail():
    if request.endpoint != "dwp.hr_form_detail" or request.method != "GET":
        return None

    form_id = (request.view_args or {}).get("form_id")
    if not form_id:
        return None

    form = db.session.get(HRFormRequest, form_id)
    if not form or form.form_type != "separation_notice":
        return None

    user = hr_forms._user()
    if not hr_forms._can_view(form, user):
        abort(403)

    return render_template("hr_forms/separation_detail.html", form=form, user=user)


@dwp_bp.after_request
def add_separation_notice_button(response):
    if (
        request.endpoint != "dwp.hr_forms_home"
        or response.status_code != 200
        or not response.content_type.startswith("text/html")
    ):
        return response

    user = _current_user_or_none()
    if not user or hr_forms._role(user) not in hr_forms.MANAGEMENT_ROLES:
        return response

    html = response.get_data(as_text=True)
    if 'data-separation-notice-button="1"' in html:
        return response

    start = html.find('<div class="hrf-actions">')
    if start == -1:
        return response
    end = html.find('</div>', start)
    if end == -1:
        return response

    href = url_for("dwp.hr_separation_new")
    button = (
        f'<a class="hrf-btn" data-separation-notice-button="1" '
        f'style="background:#dc2626;color:#fff" href="{href}">'
        'Employee Separation Notice</a>'
    )
    html = html[:end] + button + html[end:]
    response.set_data(html)
    response.headers["Content-Length"] = len(response.get_data())
    return response


def _p(text, style):
    return Paragraph(escape(str(text or "-")).replace("\n", "<br/>"), style)


def _checked_list(reasons, selected, style):
    return Paragraph(
        "<br/>".join(
            f"{'[X]' if reason == selected else '[ ]'} {escape(reason)}"
            for reason in reasons
        ),
        style,
    )


def _separation_notice_pdf(form):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SepTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="SepBody",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#111827"),
    ))
    styles.add(ParagraphStyle(
        name="SepSmall",
        parent=styles["BodyText"],
        fontSize=7.2,
        leading=8.8,
        textColor=colors.HexColor("#374151"),
    ))
    styles.add(ParagraphStyle(
        name="SepInstruction",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.2,
        textColor=colors.HexColor("#dc2626"),
        spaceBefore=5,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="SepSection",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        spaceBefore=4,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="SepBlue",
        parent=styles["BodyText"],
        fontName="Helvetica-BoldOblique",
        fontSize=8.2,
        leading=10,
        textColor=colors.HexColor("#1d4e89"),
    ))

    data = form.data
    filename = secure_filename(
        f"Employee-Separation-Notice-{hr_forms._name(form.subject_user)}-{form.id}.pdf"
    )
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.38 * inch,
        leftMargin=0.38 * inch,
        topMargin=0.38 * inch,
        bottomMargin=0.38 * inch,
        title="Employee Separation Notice",
    )

    story = [
        Paragraph("Boston Pie, Inc.", styles["SepSmall"]),
        Paragraph("Separation Notice", styles["SepTitle"]),
    ]

    meta_top = Table(
        [[
            "MANAGER:",
            _p(data.get("manager_name") or hr_forms._name(form.submitter), styles["SepBody"]),
            "EMPLOYEE NAME:",
            _p(hr_forms._name(form.subject_user), styles["SepBody"]),
        ]],
        colWidths=[0.75 * inch, 2.45 * inch, 1.35 * inch, 2.5 * inch],
    )
    meta_bottom = Table(
        [[
            "COMPANY:",
            _p(data.get("company") or "BOSTON PIE INC.", styles["SepBody"]),
            "STORE#:",
            _p(form.store_number or "-", styles["SepBody"]),
            "LAST DAY WORKED:",
            _p(data.get("last_day_worked") or "-", styles["SepBody"]),
        ]],
        colWidths=[0.7 * inch, 2.0 * inch, 0.55 * inch, 0.6 * inch, 1.25 * inch, 1.95 * inch],
    )
    for meta_table, label_columns in ((meta_top, (0, 2)), (meta_bottom, (0, 2, 4))):
        style = [
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 7.6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        for col in label_columns:
            style.append(("FONTNAME", (col, 0), (col, 0), "Helvetica-Bold"))
        meta_table.setStyle(TableStyle(style))
        story.append(meta_table)
    story.append(Paragraph(
        "Instructions: This form is to be completed by the supervisor of the separating employee. "
        "Supervisors should obtain employee's signature and statement of reason for separation.",
        styles["SepInstruction"],
    ))
    story.append(Paragraph("REASON FOR SEPARATION", styles["SepSection"]))
    story.append(Paragraph(
        "In addition to checking reason for separation, give full explanation below. If separation is for another job, "
        "include company name and starting date if known. If the employee does not give notice of voluntary separation, "
        "note when and how it was determined the employee was separated and include other relevant information.",
        styles["SepSmall"],
    ))
    story.append(Spacer(1, 5))

    selected_type = str(data.get("separation_type") or "").lower()
    selected_reason = data.get("separation_reason")
    voluntary_left = VOLUNTARY_REASONS[:8]
    voluntary_right = VOLUNTARY_REASONS[8:]
    involuntary_left = INVOLUNTARY_REASONS[:5]
    involuntary_right = INVOLUNTARY_REASONS[5:]

    reasons_table = Table([
        [Paragraph("VOLUNTARY", styles["SepBlue"]),
         _checked_list(voluntary_left, selected_reason if selected_type == "voluntary" else None, styles["SepSmall"]),
         _checked_list(voluntary_right, selected_reason if selected_type == "voluntary" else None, styles["SepSmall"])],
        [Paragraph("INVOLUNTARY", styles["SepBlue"]),
         _checked_list(involuntary_left, selected_reason if selected_type == "involuntary" else None, styles["SepSmall"]),
         _checked_list(involuntary_right, selected_reason if selected_type == "involuntary" else None, styles["SepSmall"])],
    ], colWidths=[1.25 * inch, 2.9 * inch, 2.9 * inch])
    reasons_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#6b7280")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(reasons_table)

    if selected_reason == "Transfer":
        story.append(Paragraph(
            f"Transfer - Date: {escape(str(data.get('transfer_date') or '-'))}  Store: {escape(str(data.get('transfer_store') or '-'))}",
            styles["SepSmall"],
        ))

    detail_rows = [
        [Paragraph("Explain reason given above in detail.", styles["SepSmall"]), _p(data.get("explanation"), styles["SepBody"])],
        [Paragraph("Employee's statement of reason for separation.", styles["SepSmall"]), _p(data.get("employee_statement") or "No statement provided", styles["SepBody"])],
        [Paragraph("Did the employee give notice?", styles["SepSmall"]), _p(f"{str(data.get('notice_given') or '').upper()} - To Whom: {data.get('notice_to_whom') or '-'}", styles["SepBody"])],
        [Paragraph("Is employee eligible for rehire?", styles["SepSmall"]), _p(f"{str(data.get('eligible_for_rehire') or '').upper()} - {data.get('rehire_explanation') or 'No conditions noted'}", styles["SepBody"])],
    ]
    details = Table(detail_rows, colWidths=[2.2 * inch, 4.85 * inch])
    details.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#6b7280")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([Spacer(1, 6), details, Spacer(1, 7)])

    story.append(Paragraph(
        "I certify that the information furnished above is true and correct and authorize the release of this document as requested.",
        styles["SepInstruction"],
    ))

    signatures = Table([
        ["EMPLOYEE SIGNATURE", _p(data.get("employee_signature") or "Not provided", styles["SepBody"]), "DATE", _p(data.get("employee_signature_date") or "-", styles["SepBody"])],
        ["MANAGER'S SIGNATURE", _p(data.get("manager_signature"), styles["SepBody"]), "DATE", _p(data.get("manager_signature_date"), styles["SepBody"])],
    ], colWidths=[1.35 * inch, 3.05 * inch, 0.45 * inch, 1.35 * inch])
    signatures.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("LINEABOVE", (1, 0), (1, -1), 0.55, colors.HexColor("#6b7280")),
        ("LINEABOVE", (3, 0), (3, -1), 0.55, colors.HexColor("#6b7280")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.3),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(signatures)
    story.extend([
        Spacer(1, 5),
        Paragraph(f"Confidential HR Record - BPI Ops Form HR-{form.id}", styles["SepSmall"]),
    ])

    doc.build(story)
    content = buffer.getvalue()
    buffer.close()
    return {
        "filename": filename,
        "content": content,
        "mime_type": "application/pdf",
    }


_ORIGINAL_MAKE_HR_FORM_PDF = getattr(
    hr_forms_pdf_email,
    "_make_hr_form_pdf_before_separation",
    hr_forms_pdf_email.make_hr_form_pdf,
)
hr_forms_pdf_email._make_hr_form_pdf_before_separation = _ORIGINAL_MAKE_HR_FORM_PDF


def make_hr_form_pdf(form):
    if form.form_type == "separation_notice":
        return _separation_notice_pdf(form)
    return _ORIGINAL_MAKE_HR_FORM_PDF(form)


hr_forms_pdf_email.make_hr_form_pdf = make_hr_form_pdf


@dwp_bp.route("/hr-forms/<int:form_id>/pdf")
def hr_form_pdf_download(form_id):
    user = hr_forms._user()
    form = HRFormRequest.query.get_or_404(form_id)
    if not hr_forms._can_view(form, user):
        abort(403)

    attachment = hr_forms_pdf_email.make_hr_form_pdf(form)
    return send_file(
        BytesIO(attachment["content"]),
        mimetype=attachment.get("mime_type") or "application/pdf",
        as_attachment=True,
        download_name=attachment["filename"],
    )
