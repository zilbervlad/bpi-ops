import json
from datetime import datetime

from flask import abort, flash, redirect, request, session, url_for

from app import db
from app.dwp import dwp_bp
from app.models import User
import app.dwp.hr_forms as hr_forms_module
from app.dwp.hr_forms import (
    HRFormRequest,
    HR_EMAIL,
    PAYROLL_EMAIL,
    _active_users_for,
    _add_event,
    _email,
    _name,
    _next_time_off_status,
    _safe_email,
    _user,
)


TIME_OFF_PROXY_ROLES = {"manager", "general_manager", "admin", "hr"}


def _role(user=None):
    """Use the active account/session role before the underlying user row role."""
    user = user or _user()
    return (
        session.get("account_role")
        or session.get("user_role")
        or getattr(user, "role", None)
        or ""
    ).strip().lower()


# Make the original HR Forms module use the same role resolution. This matters
# for admin accounts whose underlying user row may still carry a store role.
hr_forms_module._role = _role


def _proxy_subjects(user):
    if _role(user) not in TIME_OFF_PROXY_ROLES:
        return []

    role = _role(user)
    if role in {"admin", "hr"}:
        return (
            User.query
            .filter(User.is_active.is_(True))
            .order_by(User.store_number.asc(), User.name.asc())
            .all()
        )

    return _active_users_for(user)


def _notify_proxy_time_off(form):
    link = url_for("dwp.hr_form_detail", form_id=form.id, _external=True)
    body = f"""A time-off request was submitted on behalf of a team member.

Team Member: {_name(form.subject_user)}
Store: {form.store_number or '—'}
Submitted By: {_name(form.submitter)}
Status: Submitted to HR / Payroll

Open: {link}
"""

    recipients = {HR_EMAIL, PAYROLL_EMAIL, _email(form.subject_user)}
    admins = User.query.filter(
        User.is_active.is_(True),
        User.role == "admin",
    ).all()
    recipients.update(_email(row) for row in admins)

    for email in recipients:
        _safe_email(
            email,
            f"Time-Off Request Submitted - {_name(form.subject_user)}",
            body,
        )


@dwp_bp.context_processor
def inject_hr_form_helpers():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    if not user:
        return {
            "can_submit_time_off_for_others": False,
            "time_off_subject_users": [],
        }

    return {
        "can_submit_time_off_for_others": _role(user) in TIME_OFF_PROXY_ROLES,
        "time_off_subject_users": _proxy_subjects(user),
    }


@dwp_bp.before_request
def enhanced_time_off_submission():
    if request.endpoint != "dwp.hr_time_off_new" or request.method != "POST":
        return None

    user = _user()
    role = _role(user)
    subject = user

    requested_subject_id = request.form.get("subject_user_id", type=int)
    if requested_subject_id and requested_subject_id != user.id:
        if role not in TIME_OFF_PROXY_ROLES:
            abort(403)

        allowed = _proxy_subjects(user)
        subject = next((row for row in allowed if row.id == requested_subject_id), None)
        if not subject:
            flash("Choose a valid active team member.", "error")
            return redirect(url_for("dwp.hr_time_off_new"))

    dates = [
        value.strip()
        for value in request.form.getlist("requested_date")
        if value.strip()
    ]
    hours = [value.strip() for value in request.form.getlist("requested_hours")]

    if not dates:
        flash("Enter at least one requested date.", "error")
        return redirect(url_for("dwp.hr_time_off_new"))

    signature = request.form.get("employee_signature", "").strip()
    if not signature:
        flash("Type the electronic signature before submitting.", "error")
        return redirect(url_for("dwp.hr_time_off_new"))

    rows = [
        {
            "date": day,
            "hours": hours[index] if index < len(hours) else "",
        }
        for index, day in enumerate(dates)
    ]

    submitted_for_other = subject.id != user.id
    if submitted_for_other:
        status = "submitted_to_hr_payroll"
        approval_role = None
    else:
        status, approval_role = _next_time_off_status(user)

    form = HRFormRequest(
        form_type="time_off",
        submitter_id=user.id,
        subject_user_id=subject.id,
        store_number=subject.store_number,
        status=status,
        approval_role=approval_role,
        data_json=json.dumps({
            "leave_type": request.form.get("leave_type", ""),
            "sick_reason": request.form.get("sick_reason", ""),
            "dates": rows,
            "employee_signature": signature,
            "comments": request.form.get("comments", "").strip(),
            "submitted_on_behalf": submitted_for_other,
        }),
    )

    db.session.add(form)
    db.session.flush()

    if submitted_for_other:
        _add_event(
            form,
            user,
            "submitted_on_behalf",
            "Sent to team member, HR, Admin, and Payroll",
        )
    else:
        _add_event(form, user, "submitted", f"Routed to {approval_role}")

    db.session.commit()

    if submitted_for_other:
        _notify_proxy_time_off(form)
        flash(
            "Time-off request was sent to the team member, HR, Admin, and Payroll.",
            "success",
        )
    else:
        from app.dwp.hr_forms import _notify_submission

        _notify_submission(form)
        flash("Time-off request submitted.", "success")

    return redirect(url_for("dwp.hr_form_detail", form_id=form.id))


@dwp_bp.record_once
def register_global_hr_navigation(state):
    app = state.app

    @app.after_request
    def add_global_hr_navigation(response):
        if (
            response.status_code != 200
            or not response.content_type.startswith("text/html")
            or not session.get("user_id")
        ):
            return response

        html = response.get_data(as_text=True)
        if 'data-global-hr-nav="1"' in html:
            return response

        role = (
            session.get("account_role")
            or session.get("user_role")
            or ""
        ).strip().lower()

        # Shared store-tablet accounts must not receive employee/HR navigation.
        if role == "store":
            return response

        endpoint = request.endpoint or ""
        my_docs_active = " active" if endpoint.startswith("hr_documents") and endpoint not in {
            "hr_documents.index",
            "hr_documents.new_document",
            "hr_documents.detail",
        } else ""
        hr_forms_active = " active" if endpoint in {
            "dwp.hr_forms_home",
            "dwp.hr_time_off_new",
            "dwp.hr_pay_change_new",
            "dwp.hr_form_detail",
            "dwp.hr_form_decision",
            "dwp.hr_form_acknowledge",
        } else ""

        nav = f"""
        <div class="nav-section global-hr-nav" data-global-hr-nav="1">
            <div class="nav-section-label">HR</div>
            <a href="/hr-documents/my" class="global-hr-my-documents{my_docs_active}">
                <span class="nav-icon">◨</span>
                <span>My Documents</span>
            </a>
            <a href="/dwp/hr-forms" class="global-hr-forms{hr_forms_active}">
                <span class="nav-icon">□</span>
                <span>HR Forms</span>
            </a>
        </div>
        """

        marker = '<div class="nav-section nav-logout-section">'
        if marker in html:
            html = html.replace(marker, nav + marker, 1)

        cleanup = """
        <style>.hr-forms-shortcut{display:none!important}</style>
        <script>
        document.addEventListener("DOMContentLoaded", function () {
            const globalNav = document.querySelector(".global-hr-nav");
            if (!globalNav) return;

            document.querySelectorAll('.sidebar a[href="/hr-documents/my"]').forEach(function (link) {
                if (!globalNav.contains(link)) {
                    const section = link.closest(".nav-section");
                    link.remove();
                    if (section && !section.querySelector("a")) section.remove();
                }
            });

            const hrEndpoints = [
                "/dwp/hr-forms",
                "/dwp/hr-forms/time-off/new",
                "/dwp/hr-forms/pay-change/new"
            ];
            if (hrEndpoints.some(function (path) { return window.location.pathname === path || window.location.pathname.startsWith("/dwp/hr-forms/"); })) {
                document.querySelectorAll('.sidebar a[href="/dwp/"]').forEach(function (link) {
                    link.classList.remove("active");
                });
            }
        });
        </script>
        """

        if "</body>" in html:
            html = html.replace("</body>", cleanup + "\n</body>", 1)

        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())
        return response
