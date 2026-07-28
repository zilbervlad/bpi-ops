import json

from flask import abort, flash, redirect, request, url_for

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


@dwp_bp.before_request
def submit_pay_change_acknowledgement_first():
    """Create pay-change forms without routing them to Payroll/HR/Admin before TM acknowledgement."""
    if request.endpoint != "dwp.hr_pay_change_new" or request.method != "POST":
        return None

    user = hr_forms._user()
    if hr_forms._role(user) not in hr_forms.MANAGEMENT_ROLES:
        abort(403)

    users = hr_forms._active_users_for(user)
    subject_id = request.form.get("subject_user_id", type=int)
    subject = next((row for row in users if row.id == subject_id), None)
    if not subject:
        flash("Choose a valid active team member.", "error")
        return redirect(url_for("dwp.hr_pay_change_new"))

    payload = {key: request.form.get(key, "").strip() for key in [
        "change_type", "effective_date", "from_position", "to_position", "from_rate", "to_rate",
        "from_store", "to_store", "personal_time_action", "reason", "comments", "cash_hourly_wage",
        "tip_credit", "manager_signature",
    ]}

    if not payload["change_type"] or not payload["effective_date"] or not payload["manager_signature"]:
        flash("Change type, effective date, and manager signature are required.", "error")
        return redirect(url_for("dwp.hr_pay_change_new"))

    form = hr_forms.HRFormRequest(
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
    hr_forms._add_event(
        form,
        user,
        "submitted",
        "Sent to team member for acknowledgement before Payroll, HR, and Admin routing",
    )
    db.session.commit()

    hr_forms._notify_submission(form)
    flash(
        "Position/rate/store change sent to the team member for acknowledgement. Payroll, HR, and Admin will receive it after it is signed.",
        "success",
    )
    return redirect(url_for("dwp.hr_form_detail", form_id=form.id))


@dwp_bp.after_request
def show_pay_change_acknowledgement_first_language(response):
    if (
        request.endpoint == "dwp.hr_pay_change_new"
        and request.method == "GET"
        and response.status_code == 200
        and response.content_type.startswith("text/html")
    ):
        html = response.get_data(as_text=True)
        html = html.replace(
            "Submitting sends the form immediately to the selected team member, HR, and Payroll. The team member must review and acknowledge the full notice above.",
            "Submitting sends the form to the selected team member for acknowledgement in BPI Connect or BPI Ops. Payroll, HR, and Admin receive the completed signed PDF only after acknowledgement.",
        )
        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())
    return response


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
            hr_forms._add_event(
                form_after,
                form_after.subject_user,
                "routed_after_acknowledgement",
                "Completed PDF emailed to Payroll, HR, and Admin",
            )
            db.session.commit()
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
