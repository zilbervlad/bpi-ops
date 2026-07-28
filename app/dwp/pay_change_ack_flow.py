from flask import url_for

from app import db
from app.dwp import dwp_bp
from app.dwp import hr_forms, hr_forms_pdf_email, connect_unified_documents
from app.models import User


_original_notify_submission = hr_forms._notify_submission
_original_notify_decision = hr_forms._notify_decision
_original_connect_acknowledge = connect_unified_documents.connect_acknowledge_document_unified


def _final_recipient_emails():
    recipients = {
        hr_forms.HR_EMAIL,
        hr_forms.PAYROLL_EMAIL,
    }

    admin_hr_users = User.query.filter(
        User.is_active.is_(True),
        User.role.in_(["admin", "hr"]),
    ).all()

    recipients.update(hr_forms._email(user) for user in admin_hr_users)
    return {email for email in recipients if email}


def _notify_pay_change_waiting_for_ack(form):
    """At submission, notify only the team member. Final routing waits for acknowledgement."""
    subject_email = hr_forms._email(form.subject_user)
    if not subject_email:
        return

    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        "A Position / Rate / Store Change form is waiting for your acknowledgement.\n\n"
        f"Team Member: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '—'}\n\n"
        "Review the form and acknowledge it in BPI Connect or BPI Ops. "
        "It will not be sent to Payroll, HR, or Admin until you acknowledge it.\n\n"
        "The current form PDF is attached.\n\n"
        f"Open: {link}\n"
    )

    hr_forms_pdf_email._send(
        subject_email,
        f"Acknowledgement Required - Position/Rate/Store Change - {hr_forms._name(form.subject_user)}",
        body,
        form,
    )


def _notify_pay_change_final(form):
    """After acknowledgement, email the completed signed PDF to Payroll, HR, and Admin."""
    if form.form_type != "pay_change" or form.status != "completed" or not form.subject_acknowledged_at:
        return

    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = (
        "A Position / Rate / Store Change form has been acknowledged and is ready for processing.\n\n"
        f"Team Member: {hr_forms._name(form.subject_user)}\n"
        f"Store: {form.store_number or '—'}\n"
        f"Submitted By: {hr_forms._name(form.submitter)}\n"
        f"Acknowledged By: {form.data.get('team_member_signature') or hr_forms._name(form.subject_user)}\n"
        f"Acknowledged At: {form.subject_acknowledged_at.strftime('%m/%d/%Y %I:%M %p')}\n\n"
        "The completed, signed PDF is attached.\n\n"
        f"Open: {link}\n"
    )

    subject = f"Completed Position/Rate/Store Change - {hr_forms._name(form.subject_user)}"
    for email in _final_recipient_emails():
        hr_forms_pdf_email._send(email, subject, body, form)


def _notify_submission_after_ack_flow(form):
    if form.form_type == "pay_change":
        _notify_pay_change_waiting_for_ack(form)
        return
    return _original_notify_submission(form)


def _notify_decision_after_ack_flow(form):
    if form.form_type == "pay_change":
        _notify_pay_change_final(form)
        return
    return _original_notify_decision(form)


def connect_acknowledge_after_ack_flow(recipient_id):
    kind, item_id = connect_unified_documents._decode_item_id(recipient_id)
    should_send_final = False

    if kind == "hr_form":
        form_before = db.session.get(hr_forms.HRFormRequest, item_id)
        should_send_final = bool(
            form_before
            and form_before.form_type == "pay_change"
            and not form_before.subject_acknowledged_at
        )

    response = _original_connect_acknowledge(recipient_id)

    if should_send_final:
        form_after = db.session.get(hr_forms.HRFormRequest, item_id)
        if form_after and form_after.subject_acknowledged_at and form_after.status == "completed":
            _notify_pay_change_final(form_after)

    return response


# Route functions resolve these globals at runtime.
hr_forms._notify_submission = _notify_submission_after_ack_flow
hr_forms._notify_decision = _notify_decision_after_ack_flow


@dwp_bp.record_once
def install_pay_change_ack_flow(state):
    # The unified Connect handler is installed by connect_unified_documents first.
    state.app.view_functions[
        "hr_documents.connect_acknowledge_document"
    ] = connect_acknowledge_after_ack_flow
