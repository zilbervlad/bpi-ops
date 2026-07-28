from io import BytesIO
from xml.sax.saxutils import escape

from flask import url_for
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

from app.models import Store, User
from app.dwp import dwp_bp
from app.dwp import hr_forms
from app.dwp import hr_forms_enhancements


def _display(value):
    if value is None or value == "":
        return "—"
    return str(value).replace("_", " ").title()


def _money(value):
    value = str(value or "").strip()
    if not value:
        return "—"
    return value if value.startswith("$") else f"${value}"


def make_hr_form_pdf(form):
    """Generate the current HR form as a clean PDF attachment."""
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="HRTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name="HRSmall",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
    ))
    styles.add(ParagraphStyle(
        name="HRBody",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=13,
    ))

    title = "Request for Time Off" if form.form_type == "time_off" else "Position / Rate / Store Change Form"
    filename = secure_filename(f"{title}-{hr_forms._name(form.subject_user)}-{form.id}.pdf")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=title,
    )

    data = form.data
    story = [
        Paragraph("Boston Pie, Inc.", styles["HRSmall"]),
        Paragraph(title, styles["HRTitle"]),
    ]

    meta = [
        ["Team Member", escape(hr_forms._name(form.subject_user)), "Store", escape(str(form.store_number or "—"))],
        ["Submitted By", escape(hr_forms._name(form.submitter)), "Status", escape(_display(form.status))],
        ["Submitted", form.created_at.strftime("%m/%d/%Y %I:%M %p"), "Form ID", f"HR-{form.id}"],
    ]
    meta_table = Table(meta, colWidths=[1.05 * inch, 2.35 * inch, 0.85 * inch, 2.35 * inch])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#e2e8f0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([meta_table, Spacer(1, 14)])

    if form.form_type == "time_off":
        rows = [["Leave Type", _display(data.get("leave_type"))]]
        if data.get("sick_reason"):
            rows.append(["Sick-Time Reason", escape(str(data.get("sick_reason")))])
        request_rows = [["Date Requested", "Hours"]]
        for item in data.get("dates", []):
            request_rows.append([escape(str(item.get("date") or "—")), escape(str(item.get("hours") or "—"))])
        story.append(Paragraph("Requested Dates", styles["Heading3"]))
        dates_table = Table(request_rows, colWidths=[3.4 * inch, 2.0 * inch])
        dates_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend([dates_table, Spacer(1, 12)])
        details = rows + [
            ["Comments", escape(str(data.get("comments") or "None"))],
            ["Electronic Signature", escape(str(data.get("employee_signature") or "—"))],
        ]
    else:
        details = [
            ["Change Type", _display(data.get("change_type"))],
            ["Effective Date", escape(str(data.get("effective_date") or "—"))],
            ["Position From", escape(str(data.get("from_position") or "—"))],
            ["Position To", escape(str(data.get("to_position") or "—"))],
            ["Rate From", _money(data.get("from_rate"))],
            ["Rate To", _money(data.get("to_rate"))],
            ["Store From", escape(str(data.get("from_store") or "—"))],
            ["Store To", escape(str(data.get("to_store") or "—"))],
            ["Personal Time", _display(data.get("personal_time_action"))],
            ["Reason", _display(data.get("reason"))],
            ["Comments", escape(str(data.get("comments") or "None"))],
            ["Cash Hourly Driving Wage", _money(data.get("cash_hourly_wage"))],
            ["Tip Credit", _money(data.get("tip_credit"))],
            ["Manager / Supervisor Signature", escape(str(data.get("manager_signature") or "—"))],
            ["Team Member Signature", escape(str(data.get("team_member_signature") or "Pending acknowledgement"))],
        ]

    detail_table = Table(details, colWidths=[2.05 * inch, 4.55 * inch])
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([detail_table, Spacer(1, 14)])

    if form.decision_comment or form.approved_by:
        story.append(Paragraph(
            f"Decision by: {escape(hr_forms._name(form.approved_by)) if form.approved_by else '—'}<br/>"
            f"Decision comments: {escape(str(form.decision_comment or 'None'))}",
            styles["HRBody"],
        ))

    story.extend([
        Spacer(1, 12),
        Paragraph("Confidential HR Record · Generated by BPI Ops", styles["HRSmall"]),
    ])
    doc.build(story)
    content = buffer.getvalue()
    buffer.close()
    return {
        "filename": filename,
        "content": content,
        "mime_type": "application/pdf",
    }


def _send(to_email, subject, body, form):
    if not to_email:
        return False
    try:
        attachment = make_hr_form_pdf(form)
        hr_forms.send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            attachments=[attachment],
        )
        return True
    except Exception:
        return False


def _notify_submission_with_pdf(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        "A new BPI Ops HR form requires attention.\n\n"
        f"Form: {form.form_type.replace('_', ' ').title()}\n"
        f"Team Member: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '—'}\n"
        f"Status: {form.status.replace('_', ' ').title()}\n\n"
        "The current form PDF is attached.\n\n"
        f"Open: {link}\n"
    )
    for email in hr_forms._approver_emails(form):
        _send(email, f"HR Form Approval Required - {hr_forms._name(form.subject_user)}", body, form)
    if form.form_type == "pay_change":
        for email in {hr_forms.HR_EMAIL, hr_forms.PAYROLL_EMAIL, hr_forms._email(form.subject_user)}:
            _send(email, f"Position/Rate/Store Change - {hr_forms._name(form.subject_user)}", body, form)


def _notify_decision_with_pdf(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        f"A BPI Ops HR form has been {form.status}.\n\n"
        f"Form: {form.form_type.replace('_', ' ').title()}\n"
        f"Team Member: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '—'}\n"
        f"Decision: {form.status.title()}\n"
        f"Comments: {form.decision_comment or 'None'}\n\n"
        "The updated form PDF is attached.\n\n"
        f"Open: {link}\n"
    )
    recipients = {
        hr_forms.HR_EMAIL,
        hr_forms.PAYROLL_EMAIL,
        hr_forms._email(form.subject_user),
        hr_forms._email(form.submitter),
    }
    store = Store.query.filter_by(store_number=form.store_number).first()
    if store:
        supervisors = User.query.filter_by(role="supervisor", area_name=store.area_name, is_active=True).all()
        recipients.update(hr_forms._email(row) for row in supervisors)
    for email in recipients:
        _send(email, f"HR Form {form.status.title()} - {hr_forms._name(form.subject_user)}", body, form)


def _notify_proxy_with_pdf(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        "A time-off request was submitted on behalf of a team member.\n\n"
        f"Team Member: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '—'}\n"
        f"Submitted By: {hr_forms._name(form.submitter)}\n"
        "Status: Submitted to HR / Payroll\n\n"
        "The completed request PDF is attached.\n\n"
        f"Open: {link}\n"
    )
    recipients = {hr_forms.HR_EMAIL, hr_forms.PAYROLL_EMAIL, hr_forms._email(form.subject_user)}
    admins = User.query.filter(User.is_active.is_(True), User.role == "admin").all()
    recipients.update(hr_forms._email(row) for row in admins)
    for email in recipients:
        _send(email, f"Time-Off Request Submitted - {hr_forms._name(form.subject_user)}", body, form)


# Route functions resolve these module globals at runtime, so replacing them here
# upgrades every submission, decision, acknowledgement, and proxy email.
hr_forms._notify_submission = _notify_submission_with_pdf
hr_forms._notify_decision = _notify_decision_with_pdf
hr_forms_enhancements._notify_proxy_time_off = _notify_proxy_with_pdf
