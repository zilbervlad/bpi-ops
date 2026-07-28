from datetime import datetime
from io import BytesIO
import json

from flask import jsonify, request, send_file

from app import db
from app.dwp import dwp_bp
from app.dwp import connect_unified_documents as unified
from app.dwp import hr_forms, hr_forms_pdf_email
from app.dwp.routes import make_dwp_pdf
from app.hr_documents.routes import require_connect_integration_secret
from app.models import DWPRecord, HRDocumentRecipient, User


def _identity_users(requested_user_id):
    user = db.session.get(User, requested_user_id)
    if not user or not user.is_active:
        return []

    email = str(getattr(user, "email", "") or "").strip().lower()
    notification_email = str(getattr(user, "notification_email", "") or "").strip().lower()
    candidates = {user.id: user}

    identities = {value for value in [email, notification_email] if value}
    if identities:
        for candidate in User.query.filter(User.is_active.is_(True)).all():
            candidate_emails = {
                str(getattr(candidate, "email", "") or "").strip().lower(),
                str(getattr(candidate, "notification_email", "") or "").strip().lower(),
            }
            if identities.intersection({value for value in candidate_emails if value}):
                candidates[candidate.id] = candidate

    return list(candidates.values())


def _identity_ids(requested_user_id):
    return {user.id for user in _identity_users(requested_user_id)}


def connect_user_documents_identity_safe(user_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    users = _identity_users(user_id)
    if not users:
        return jsonify({"success": False, "error": "BPI Ops user not found or inactive."}), 404

    documents = []
    seen = set()
    for user in users:
        for row in unified._hr_document_rows(user) + unified._dwp_rows(user) + unified._hr_form_rows(user):
            key = (row.get("document_kind"), row.get("document_id"))
            if key in seen:
                continue
            seen.add(key)
            documents.append(row)

    documents.sort(key=lambda row: row.get("assigned_at") or "", reverse=True)
    documents.sort(key=lambda row: row.get("status") == "acknowledged")

    primary = users[0]
    return jsonify({
        "success": True,
        "user": {"id": primary.id, "name": primary.name},
        "documents": documents,
    })


def connect_document_file_identity_safe(recipient_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    requested_user_id = request.args.get("bpi_ops_user_id", type=int)
    allowed_ids = _identity_ids(requested_user_id)
    if not allowed_ids:
        return jsonify({"success": False, "error": "BPI Ops user not found or inactive."}), 404

    kind, item_id = unified._decode_item_id(recipient_id)

    if kind == "hr_document":
        recipient = db.session.get(HRDocumentRecipient, item_id)
        if not recipient or recipient.user_id not in allowed_ids:
            return jsonify({"success": False, "error": "Document assignment not found."}), 404
        document = recipient.document
        if not document or not document.is_active:
            return jsonify({"success": False, "error": "Document is unavailable."}), 404
        response = send_file(
            BytesIO(document.file_data),
            mimetype=document.content_type or "application/octet-stream",
            as_attachment=False,
            download_name=document.original_filename,
        )
    elif kind == "dwp":
        record = db.session.get(DWPRecord, item_id)
        if not record or record.team_member_id not in allowed_ids:
            return jsonify({"success": False, "error": "DWP record not found."}), 404
        response = send_file(
            make_dwp_pdf(record),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"DWP-{record.id}.pdf",
        )
    else:
        form = db.session.get(hr_forms.HRFormRequest, item_id)
        if not form or form.subject_user_id not in allowed_ids:
            return jsonify({"success": False, "error": "HR form not found."}), 404
        pdf = hr_forms_pdf_email.make_hr_form_pdf(form)
        response = send_file(
            BytesIO(pdf["content"]),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=pdf["filename"],
        )

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def connect_acknowledge_identity_safe(recipient_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    try:
        requested_user_id = int(data.get("bpi_ops_user_id") or 0)
    except (TypeError, ValueError):
        requested_user_id = 0

    acknowledged_name = str(data.get("acknowledged_name") or "").strip()
    confirmed = data.get("confirmed") is True
    allowed_ids = _identity_ids(requested_user_id)

    if not allowed_ids:
        return jsonify({"success": False, "error": "BPI Ops user not found or inactive."}), 404
    if not acknowledged_name:
        return jsonify({"success": False, "error": "Please type your name."}), 400
    if not confirmed:
        return jsonify({"success": False, "error": "Document acknowledgement must be confirmed."}), 400

    kind, item_id = unified._decode_item_id(recipient_id)
    now = datetime.utcnow()

    if kind == "hr_document":
        recipient = db.session.get(HRDocumentRecipient, item_id)
        if not recipient or recipient.user_id not in allowed_ids:
            return jsonify({"success": False, "error": "Document assignment not found."}), 404
        if recipient.status != "acknowledged":
            recipient.status = "acknowledged"
            recipient.acknowledged_at = now
            recipient.acknowledged_name = acknowledged_name
            recipient.acknowledged_ip = (request.headers.get("X-Connect-Client-IP") or request.remote_addr or "")[:80]
            recipient.acknowledged_user_agent = (request.headers.get("X-Connect-User-Agent") or request.user_agent.string or "")[:255]
        acknowledged_at = recipient.acknowledged_at
        saved_name = recipient.acknowledged_name
    elif kind == "dwp":
        record = db.session.get(DWPRecord, item_id)
        if not record or record.team_member_id not in allowed_ids:
            return jsonify({"success": False, "error": "DWP record not found."}), 404
        if not record.acknowledged_at:
            record.acknowledged_at = now
            record.acknowledged_by_id = record.team_member_id
            record.acknowledged_name = acknowledged_name
            record.status = "acknowledged"
        acknowledged_at = record.acknowledged_at
        saved_name = record.acknowledged_name
    else:
        form = db.session.get(hr_forms.HRFormRequest, item_id)
        if not form or form.subject_user_id not in allowed_ids:
            return jsonify({"success": False, "error": "HR form not found."}), 404
        if form.form_type != "pay_change":
            return jsonify({"success": False, "error": "This form does not require team-member acknowledgement."}), 400

        first_acknowledgement = not form.subject_acknowledged_at
        if first_acknowledgement:
            payload = form.data
            payload["team_member_signature"] = acknowledged_name
            payload["tip_credit_acknowledged"] = True
            form.data_json = json.dumps(payload)
            form.subject_acknowledged_at = now
            form.status = "completed"
            hr_forms._add_event(form, form.subject_user, "acknowledged_in_connect", acknowledged_name)

        acknowledged_at = form.subject_acknowledged_at
        saved_name = form.data.get("team_member_signature")
        db.session.commit()

        if first_acknowledgement:
            try:
                from app.dwp.pay_change_ack_flow import _notify_pay_change_final
                _notify_pay_change_final(form)
            except Exception:
                pass

        return jsonify({
            "success": True,
            "recipient": {
                "id": recipient_id,
                "status": "acknowledged",
                "acknowledged_at": unified._iso(acknowledged_at),
                "acknowledged_name": saved_name,
            },
        })

    db.session.commit()
    return jsonify({
        "success": True,
        "recipient": {
            "id": recipient_id,
            "status": "acknowledged",
            "acknowledged_at": unified._iso(acknowledged_at),
            "acknowledged_name": saved_name,
        },
    })


@dwp_bp.record_once
def install_duplicate_identity_safe_connect_documents(state):
    state.app.view_functions["hr_documents.connect_user_documents"] = connect_user_documents_identity_safe
    state.app.view_functions["hr_documents.connect_document_file"] = connect_document_file_identity_safe
    state.app.view_functions["hr_documents.connect_acknowledge_document"] = connect_acknowledge_identity_safe
