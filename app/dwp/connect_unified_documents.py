import os
from datetime import datetime
from io import BytesIO

import requests
from flask import jsonify, request, send_file
from sqlalchemy import event

from app import db
from app.dwp import dwp_bp
from app.dwp.hr_forms import HRFormRequest, _name as hr_form_name
from app.dwp.hr_forms_pdf_email import make_hr_form_pdf
from app.dwp.routes import make_dwp_pdf
from app.hr_documents.routes import require_connect_integration_secret
from app.models import DWPRecord, HRDocument, HRDocumentRecipient, User


DWP_ID_OFFSET = 1_000_000_000
HR_FORM_ID_OFFSET = 2_000_000_000


def _iso(value):
    return value.isoformat() if value else None


def _decode_item_id(recipient_id):
    if recipient_id >= HR_FORM_ID_OFFSET:
        return "hr_form", recipient_id - HR_FORM_ID_OFFSET
    if recipient_id >= DWP_ID_OFFSET:
        return "dwp", recipient_id - DWP_ID_OFFSET
    return "hr_document", recipient_id


def _hr_document_rows(user):
    recipients = (
        HRDocumentRecipient.query
        .join(HRDocument)
        .filter(
            HRDocumentRecipient.user_id == user.id,
            HRDocument.is_active.is_(True),
        )
        .order_by(
            HRDocumentRecipient.status == "acknowledged",
            HRDocumentRecipient.assigned_at.desc(),
        )
        .all()
    )

    rows = []
    for recipient in recipients:
        document = recipient.document
        rows.append({
            "recipient_id": recipient.id,
            "document_id": document.id,
            "document_kind": "hr_document",
            "title": document.title,
            "description": document.description or "Assigned HR document",
            "original_filename": document.original_filename,
            "content_type": document.content_type or "application/octet-stream",
            "file_size": document.file_size or 0,
            "due_date": _iso(document.due_date),
            "status": recipient.status,
            "assigned_at": _iso(recipient.assigned_at),
            "acknowledged_at": _iso(recipient.acknowledged_at),
            "acknowledged_name": recipient.acknowledged_name,
        })
    return rows


def _dwp_rows(user):
    records = (
        DWPRecord.query
        .filter(DWPRecord.team_member_id == user.id)
        .order_by(DWPRecord.created_at.desc())
        .all()
    )

    rows = []
    for record in records:
        acknowledged = bool(record.acknowledged_at)
        rows.append({
            "recipient_id": DWP_ID_OFFSET + record.id,
            "document_id": record.id,
            "document_kind": "dwp",
            "title": f"DWP · {record.discussion_type}",
            "description": f"{record.category} · Store {record.store_number} · Conversation {record.conversation_date.strftime('%m/%d/%Y')}",
            "original_filename": f"DWP-{record.id}.pdf",
            "content_type": "application/pdf",
            "file_size": 0,
            "due_date": None,
            "status": "acknowledged" if acknowledged else "pending",
            "assigned_at": _iso(record.created_at),
            "acknowledged_at": _iso(record.acknowledged_at),
            "acknowledged_name": record.acknowledged_name,
        })
    return rows


def _hr_form_rows(user):
    forms = (
        HRFormRequest.query
        .filter(HRFormRequest.subject_user_id == user.id)
        .order_by(HRFormRequest.created_at.desc())
        .all()
    )

    rows = []
    for form in forms:
        requires_ack = form.form_type == "pay_change" and form.status == "pending_tm_acknowledgement"
        acknowledged = bool(form.subject_acknowledged_at) or not requires_ack
        title = "Request for Time Off" if form.form_type == "time_off" else "Position / Rate / Store Change"
        rows.append({
            "recipient_id": HR_FORM_ID_OFFSET + form.id,
            "document_id": form.id,
            "document_kind": "hr_form",
            "title": title,
            "description": f"Store {form.store_number or '—'} · {str(form.status or '').replace('_', ' ').title()}",
            "original_filename": f"{title}-{form.id}.pdf",
            "content_type": "application/pdf",
            "file_size": 0,
            "due_date": None,
            "status": "acknowledged" if acknowledged else "pending",
            "assigned_at": _iso(form.created_at),
            "acknowledged_at": _iso(form.subject_acknowledged_at),
            "acknowledged_name": form.data.get("team_member_signature"),
        })
    return rows


def connect_user_documents_unified(user_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        return jsonify({"success": False, "error": "BPI Ops user not found or inactive."}), 404

    documents = _hr_document_rows(user) + _dwp_rows(user) + _hr_form_rows(user)
    documents.sort(key=lambda row: row.get("assigned_at") or "", reverse=True)
    documents.sort(key=lambda row: row.get("status") == "acknowledged")

    return jsonify({
        "success": True,
        "user": {"id": user.id, "name": user.name},
        "documents": documents,
    })


def connect_document_file_unified(recipient_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    requested_user_id = request.args.get("bpi_ops_user_id", type=int)
    if not requested_user_id:
        return jsonify({"success": False, "error": "bpi_ops_user_id is required."}), 400

    kind, item_id = _decode_item_id(recipient_id)

    if kind == "hr_document":
        recipient = db.session.get(HRDocumentRecipient, item_id)
        if not recipient or recipient.user_id != requested_user_id:
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
        if not record or record.team_member_id != requested_user_id:
            return jsonify({"success": False, "error": "DWP record not found."}), 404
        response = send_file(
            make_dwp_pdf(record),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"DWP-{record.id}.pdf",
        )
    else:
        form = db.session.get(HRFormRequest, item_id)
        if not form or form.subject_user_id != requested_user_id:
            return jsonify({"success": False, "error": "HR form not found."}), 404
        pdf = make_hr_form_pdf(form)
        response = send_file(
            BytesIO(pdf["content"]),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=pdf["filename"],
        )

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def connect_acknowledge_document_unified(recipient_id):
    auth_error = require_connect_integration_secret()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    try:
        requested_user_id = int(data.get("bpi_ops_user_id") or 0)
    except (TypeError, ValueError):
        requested_user_id = 0

    acknowledged_name = (data.get("acknowledged_name") or "").strip()
    confirmed = data.get("confirmed") is True
    if not requested_user_id:
        return jsonify({"success": False, "error": "bpi_ops_user_id is required."}), 400
    if not acknowledged_name:
        return jsonify({"success": False, "error": "Please type your name."}), 400
    if not confirmed:
        return jsonify({"success": False, "error": "Document acknowledgement must be confirmed."}), 400

    kind, item_id = _decode_item_id(recipient_id)
    now = datetime.utcnow()

    if kind == "hr_document":
        recipient = db.session.get(HRDocumentRecipient, item_id)
        if not recipient or recipient.user_id != requested_user_id:
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
        if not record or record.team_member_id != requested_user_id:
            return jsonify({"success": False, "error": "DWP record not found."}), 404
        if not record.acknowledged_at:
            record.acknowledged_at = now
            record.acknowledged_by_id = requested_user_id
            record.acknowledged_name = acknowledged_name
            record.status = "acknowledged"
        acknowledged_at = record.acknowledged_at
        saved_name = record.acknowledged_name
    else:
        form = db.session.get(HRFormRequest, item_id)
        if not form or form.subject_user_id != requested_user_id:
            return jsonify({"success": False, "error": "HR form not found."}), 404
        if form.form_type != "pay_change":
            return jsonify({"success": False, "error": "This form does not require team-member acknowledgement."}), 400
        if not form.subject_acknowledged_at:
            payload = form.data
            payload["team_member_signature"] = acknowledged_name
            payload["tip_credit_acknowledged"] = True
            form.data_json = __import__("json").dumps(payload)
            form.subject_acknowledged_at = now
            form.status = "completed"
        acknowledged_at = form.subject_acknowledged_at
        saved_name = form.data.get("team_member_signature")

    db.session.commit()
    return jsonify({
        "success": True,
        "recipient": {
            "id": recipient_id,
            "status": "acknowledged",
            "acknowledged_at": _iso(acknowledged_at),
            "acknowledged_name": saved_name,
        },
    })


def _connect_notify_for_user(user, title):
    api_base = os.getenv("BPI_CONNECT_API_BASE", "").strip().rstrip("/")
    secret = os.getenv("BPI_CONNECT_INTEGRATION_SECRET", "").strip()
    email = getattr(user, "email", None) if user else None
    if not api_base or not secret or not email:
        return
    try:
        requests.post(
            f"{api_base}/api/integrations/bpi-ops/hr-documents/notify",
            json={
                "email": email,
                "document_title": title,
                "document_url": "",
                "action": "assigned",
            },
            headers={
                "Authorization": f"Bearer {secret}",
                "X-BPI-Ops-Secret": secret,
                "X-Integration-Secret": secret,
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


def _dwp_after_insert(mapper, connection, target):
    user = db.session.get(User, target.team_member_id)
    _connect_notify_for_user(user, f"New DWP: {target.discussion_type}")


def _hr_form_after_insert(mapper, connection, target):
    user = db.session.get(User, target.subject_user_id)
    title = "Request for Time Off" if target.form_type == "time_off" else "Position / Rate / Store Change"
    _connect_notify_for_user(user, title)


@dwp_bp.record_once
def install_connect_document_overrides(state):
    app = state.app
    app.view_functions["hr_documents.connect_user_documents"] = connect_user_documents_unified
    app.view_functions["hr_documents.connect_document_file"] = connect_document_file_unified
    app.view_functions["hr_documents.connect_acknowledge_document"] = connect_acknowledge_document_unified

    if not getattr(app, "_connect_hr_record_events_installed", False):
        event.listen(DWPRecord, "after_insert", _dwp_after_insert)
        event.listen(HRFormRequest, "after_insert", _hr_form_after_insert)
        app._connect_hr_record_events_installed = True
