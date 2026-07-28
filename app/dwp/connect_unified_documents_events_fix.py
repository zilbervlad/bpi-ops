import os

import requests
from sqlalchemy import event, select

from app.dwp import dwp_bp
from app.dwp.hr_forms import HRFormRequest
from app.dwp.connect_unified_documents import _dwp_after_insert, _hr_form_after_insert
from app.models import DWPRecord, User


def _notify_email(email, title):
    api_base = os.getenv("BPI_CONNECT_API_BASE", "").strip().rstrip("/")
    secret = os.getenv("BPI_CONNECT_INTEGRATION_SECRET", "").strip()
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


def _email_for_user(connection, user_id):
    row = connection.execute(
        select(User.email).where(User.id == user_id)
    ).first()
    return row[0] if row else None


def safe_dwp_after_insert(mapper, connection, target):
    _notify_email(
        _email_for_user(connection, target.team_member_id),
        f"New DWP: {target.discussion_type}",
    )


def safe_hr_form_after_insert(mapper, connection, target):
    title = (
        "Request for Time Off"
        if target.form_type == "time_off"
        else "Position / Rate / Store Change"
    )
    _notify_email(
        _email_for_user(connection, target.subject_user_id),
        title,
    )


@dwp_bp.record_once
def replace_unsafe_connect_push_hooks(state):
    try:
        event.remove(DWPRecord, "after_insert", _dwp_after_insert)
    except Exception:
        pass
    try:
        event.remove(HRFormRequest, "after_insert", _hr_form_after_insert)
    except Exception:
        pass

    event.listen(DWPRecord, "after_insert", safe_dwp_after_insert)
    event.listen(HRFormRequest, "after_insert", safe_hr_form_after_insert)
