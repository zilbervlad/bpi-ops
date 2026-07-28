from markupsafe import escape
from flask import request, url_for

from app import db
from app.dwp import dwp_bp
from app.dwp import hr_forms
from app.models import User


def _matching_user_ids(team_member):
    ids = {team_member.id}
    email = str(getattr(team_member, "email", "") or "").strip().lower()
    if email:
        matches = User.query.filter(db.func.lower(User.email) == email).all()
        ids.update(user.id for user in matches)
    return ids


def _status_label(form):
    return str(form.status or "").replace("_", " ").title() or "Submitted"


def _form_label(form):
    if form.form_type == "pay_change":
        return "Position / Rate / Store Change"
    if form.form_type == "time_off":
        return "Request for Time Off"
    return str(form.form_type or "HR Form").replace("_", " ").title()


def _forms_card(forms):
    rows = []
    for form in forms:
        status = _status_label(form)
        completed = bool(
            form.status in {"completed", "approved"}
            or form.subject_acknowledged_at
        )
        pill_class = "pill-acknowledged" if completed else "pill-pending"
        submitted = form.created_at.strftime("%m/%d/%y") if form.created_at else "—"
        acknowledged = (
            form.subject_acknowledged_at.strftime("%m/%d/%y")
            if form.subject_acknowledged_at
            else None
        )
        detail_url = url_for("dwp.hr_form_detail", form_id=form.id)
        form_name = escape(_form_label(form))
        status_text = escape(status)
        store = escape(str(form.store_number or "—"))
        ack_text = f" · Acknowledged {escape(acknowledged)}" if acknowledged else ""

        rows.append(f"""
<div class="hr-doc-item">
  <div class="hr-doc-title">
    {form_name}
    <span class="pill {pill_class}">{status_text}</span>
  </div>
  <div class="hr-doc-meta">
    Store {store} · Submitted {escape(submitted)}{ack_text}
  </div>
  <div style="margin-top:8px;">
    <a class="view-link" href="{detail_url}">Open form and PDF record</a>
  </div>
</div>
""")

    if rows:
        body = '<div class="hr-doc-list">' + "".join(rows) + "</div>"
    else:
        body = '<div class="empty">No HR forms saved for this team member yet.</div>'

    return f"""
<div class="card" style="margin-top:14px;">
  <div class="card-header">
    <div class="card-title">Employment &amp; Payroll Forms</div>
  </div>
  {body}
</div>
"""


@dwp_bp.after_request
def add_hr_forms_to_team_member_file(response):
    if (
        request.endpoint != "dwp.team_member_file"
        or request.method != "GET"
        or response.status_code != 200
        or not response.content_type.startswith("text/html")
    ):
        return response

    user_id = (request.view_args or {}).get("user_id")
    team_member = db.session.get(User, user_id) if user_id else None
    if not team_member:
        return response

    user_ids = _matching_user_ids(team_member)
    forms = (
        hr_forms.HRFormRequest.query
        .filter(hr_forms.HRFormRequest.subject_user_id.in_(user_ids))
        .order_by(hr_forms.HRFormRequest.created_at.desc())
        .all()
    )

    html = response.get_data(as_text=True)
    marker = '        </div>\n\n        <div class="card">\n            <div class="card-header">\n                <div class="card-title">DWP History</div>'
    if marker not in html:
        return response

    replacement = (
        _forms_card(forms)
        + '        </div>\n\n        <div class="card">\n            <div class="card-header">\n                <div class="card-title">DWP History</div>'
    )
    html = html.replace(marker, replacement, 1)
    response.set_data(html)
    response.headers["Content-Length"] = len(response.get_data())
    return response
