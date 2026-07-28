import json
from datetime import datetime

from flask import abort, flash, redirect, request, session, url_for

from app import db
from app.dwp import dwp_bp
from app.models import User
from app.dwp.hr_forms import (
    HRFormRequest,
    HR_EMAIL,
    PAYROLL_EMAIL,
    _active_users_for,
    _add_event,
    _email,
    _name,
    _next_time_off_status,
    _role,
    _safe_email,
    _user,
)


TIME_OFF_PROXY_ROLES = {"manager", "general_manager", "admin", "hr"}


def _proxy_subjects(user):
    if _role(user) not in TIME_OFF_PROXY_ROLES:
        return []
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

        admin_link = ""
        if role in {"admin", "hr", "supervisor"}:
            admin_link = """
            <a href="/hr-documents/" class="global-hr-admin-link">
                <span class="nav-icon">◆</span>
                <span>Manage Documents</span>
            </a>
            """

        nav = f"""
        <div class="nav-section global-hr-nav" data-global-hr-nav="1">
            <div class="nav-section-label">HR</div>
            <a href="/hr-documents/my">
                <span class="nav-icon">◨</span>
                <span>My Documents</span>
            </a>
            <a href="/dwp/hr-forms">
                <span class="nav-icon">□</span>
                <span>HR Forms</span>
            </a>
            {admin_link}
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
            document.querySelectorAll('.sidebar a[href="/hr-documents/my"], .sidebar a[href="/hr-documents/"]').forEach(function (link) {
                if (!globalNav.contains(link)) {
                    const section = link.closest(".nav-section");
                    link.remove();
                    if (section && !section.querySelector("a")) section.remove();
                }
            });
        });
        </script>
        """

        if "</body>" in html:
            html = html.replace("</body>", cleanup + "\n</body>", 1)

        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())
        return response
