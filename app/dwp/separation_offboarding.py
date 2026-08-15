"""Turn a completed Separation Notice into the employee access action.

Normal separations deactivate the selected employee in BPI Ops and immediately
sync that inactive state to BPI Connect. BPI Connect is responsible for its own
chat/group cleanup when it receives the inactive state.

A voluntary Transfer is the one exception: the employee remains active, their
BPI Ops store/area assignment is moved to the selected store, and that change is
synced to Connect instead of deactivating them.
"""

import re

from flask import current_app, flash, redirect, request, session, url_for

from app import db
from app.auth.routes import clean_access_fields, sync_user_to_bpi_connect
from app.dwp import dwp_bp, hr_forms
from app.dwp.hr_forms import HRFormRequest
from app.models import Store, User


_SUCCESS_DETAIL_RE = re.compile(r"/dwp/hr-forms/(\d+)(?:[?#].*)?$")


def _transfer_store_number():
    return str(request.form.get("transfer_store") or "").strip()


def _active_store(store_number):
    if not store_number:
        return None

    return Store.query.filter_by(
        store_number=store_number,
        is_active=True,
    ).first()


@dwp_bp.before_request
def validate_separation_transfer_target():
    """Do not allow a Transfer notice to complete without a real target store."""
    if (
        request.endpoint != "dwp.hr_separation_new"
        or request.method != "POST"
        or str(request.form.get("separation_reason") or "").strip() != "Transfer"
    ):
        return None

    target_store_number = _transfer_store_number()
    target_store = _active_store(target_store_number)

    if not target_store:
        flash(
            "Choose a valid active BPI store for the employee transfer.",
            "error",
        )
        return redirect(url_for("dwp.hr_separation_new"))

    return None


def _completed_separation_form_from_response(response):
    if response.status_code not in {302, 303}:
        return None

    location = str(response.headers.get("Location") or "")
    match = _SUCCESS_DETAIL_RE.search(location)
    if not match:
        return None

    form = db.session.get(HRFormRequest, int(match.group(1)))
    if not form or form.form_type != "separation_notice":
        return None

    return form


def _record_event(form, actor, action, note):
    try:
        hr_forms._add_event(form, actor, action, note)
    except Exception:
        current_app.logger.exception(
            "Could not add separation offboarding audit event form_id=%s",
            getattr(form, "id", None),
        )


def _apply_transfer(subject, form):
    target_store_number = str(form.data.get("transfer_store") or "").strip()
    target_store = _active_store(target_store_number)

    if not target_store:
        raise RuntimeError(
            f"Transfer target store {target_store_number or '(blank)'} is not active."
        )

    previous_store = str(subject.store_number or form.store_number or "-")
    area_name, store_number = clean_access_fields(
        subject.role,
        target_store.area_name,
        target_store.store_number,
    )

    subject.area_name = area_name
    subject.store_number = store_number
    subject.is_active = True

    return (
        f"Employee transferred from Store {previous_store} to Store "
        f"{target_store.store_number}; BPI Ops access remains active."
    )


def _apply_deactivation(subject):
    subject.is_active = False
    return "Employee deactivated in BPI Ops after completed Separation Notice."


@dwp_bp.after_request
def apply_completed_separation_offboarding(response):
    """Apply access changes only after the Separation Notice saved successfully."""
    if (
        request.endpoint != "dwp.hr_separation_new"
        or request.method != "POST"
    ):
        return response

    form = _completed_separation_form_from_response(response)
    if not form:
        return response

    subject = db.session.get(User, form.subject_user_id)
    actor_id = session.get("user_id")
    actor = db.session.get(User, actor_id) if actor_id else form.submitter

    if not subject:
        _record_event(
            form,
            actor,
            "offboarding_failed",
            "Employee account could not be found after the Separation Notice was saved.",
        )
        db.session.commit()
        flash(
            "The Separation Notice was saved, but the employee account could not be updated.",
            "warning",
        )
        return response

    is_transfer = (
        str(form.data.get("separation_type") or "").strip().lower() == "voluntary"
        and str(form.data.get("separation_reason") or "").strip() == "Transfer"
    )

    try:
        if is_transfer:
            ops_note = _apply_transfer(subject, form)
            event_action = "employee_transferred"
        else:
            ops_note = _apply_deactivation(subject)
            event_action = "employee_deactivated"

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "BPI Ops separation access update failed form_id=%s user_id=%s",
            form.id,
            subject.id,
        )
        fresh_form = db.session.get(HRFormRequest, form.id)
        fresh_actor = db.session.get(User, actor.id) if actor else None
        if fresh_form:
            _record_event(
                fresh_form,
                fresh_actor,
                "offboarding_failed",
                f"BPI Ops access update failed: {exc}",
            )
            db.session.commit()
        flash(
            "The Separation Notice was saved, but the BPI Ops access update failed. "
            "HR/Admin should review this employee immediately.",
            "warning",
        )
        return response

    sync_result = sync_user_to_bpi_connect(
        subject,
        send_invite=False,
    )

    # Refresh after the sync call so the audit event is attached to the current
    # SQLAlchemy session even if the integration helper performed network work.
    form = db.session.get(HRFormRequest, form.id)
    actor = db.session.get(User, actor.id) if actor else None

    if sync_result.get("success"):
        connect_note = (
            "BPI Connect store/access synchronized."
            if is_transfer
            else "BPI Connect deactivated; all Connect group memberships are removed by Connect cleanup."
        )
        _record_event(
            form,
            actor,
            event_action,
            f"{ops_note} {connect_note}",
        )
        db.session.commit()

        if is_transfer:
            flash(
                f"{subject.name} was transferred to Store {subject.store_number} in BPI Ops and Connect.",
                "success",
            )
        else:
            flash(
                f"{subject.name} was deactivated in BPI Ops and Connect and removed from all Connect groups.",
                "success",
            )
    else:
        error = sync_result.get("error") or "Unknown BPI Connect sync error"
        _record_event(
            form,
            actor,
            "connect_offboarding_sync_failed",
            f"{ops_note} BPI Connect sync failed: {error}",
        )
        db.session.commit()
        current_app.logger.error(
            "BPI Connect separation sync failed form_id=%s user_id=%s error=%s",
            form.id,
            subject.id,
            error,
        )
        flash(
            "BPI Ops was updated, but BPI Connect cleanup FAILED. "
            f"HR/Admin must review Connect for {subject.name}. Details: {error}",
            "warning",
        )

    return response
