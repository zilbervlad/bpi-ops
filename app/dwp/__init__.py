from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    request,
    session,
    url_for,
)

from app import db
from app.models import DWPRecord, User


dwp_bp = Blueprint("dwp", __name__, url_prefix="/dwp")


@dwp_bp.route("/<int:record_id>/delete", methods=["POST"])
def delete_record(record_id):
    """Permanently delete a DWP record. Admin and HR only."""
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None

    if not user:
        abort(403)

    role = str(getattr(user, "role", "") or "").strip().lower()
    if role not in {"admin", "hr"}:
        abort(403)

    record = DWPRecord.query.get_or_404(record_id)
    team_member_name = (
        record.team_member_name_snapshot
        or f"DWP-{record.id}"
    )

    try:
        db.session.delete(record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    flash(f"DWP record for {team_member_name} was deleted.", "success")
    return redirect(url_for("dwp.index"))


@dwp_bp.after_request
def add_admin_hr_delete_control(response):
    """Add the delete button to DWP detail pages for Admin and HR users only."""
    if (
        request.endpoint != "dwp.detail"
        or response.status_code != 200
        or not response.content_type.startswith("text/html")
    ):
        return response

    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    role = str(getattr(user, "role", "") or "").strip().lower()

    if role not in {"admin", "hr"}:
        return response

    record_id = request.view_args.get("record_id") if request.view_args else None
    if not record_id:
        return response

    html = response.get_data(as_text=True)
    delete_url = url_for("dwp.delete_record", record_id=record_id)

    delete_control = f"""
<style>
.dwp-delete-form {{ margin-left: auto; }}
.dwp-delete-btn {{
    background: #b91c1c;
    color: #fff;
}}
.dwp-delete-btn:hover {{ background: #991b1b; }}
@media (max-width: 700px) {{
    .dwp-delete-form {{ width: 100%; margin-left: 0; }}
    .dwp-delete-form .btn {{ width: 100%; }}
}}
</style>
<script>
document.addEventListener("DOMContentLoaded", function () {{
    const actionGroups = document.querySelectorAll(".dwp-detail-page .actions");
    const actions = actionGroups[actionGroups.length - 1];
    if (!actions || document.getElementById("deleteDwpForm")) return;

    const form = document.createElement("form");
    form.id = "deleteDwpForm";
    form.className = "dwp-delete-form";
    form.method = "POST";
    form.action = {delete_url!r};
    form.addEventListener("submit", function (event) {{
        const confirmed = window.confirm(
            "Delete this DWP permanently? This cannot be undone."
        );
        if (!confirmed) event.preventDefault();
    }});

    const button = document.createElement("button");
    button.type = "submit";
    button.className = "btn dwp-delete-btn";
    button.textContent = "Delete DWP";
    form.appendChild(button);
    actions.appendChild(form);
}});
</script>
"""

    if "</body>" in html:
        html = html.replace("</body>", delete_control + "\n</body>", 1)
        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())

    return response


@dwp_bp.after_request
def add_hr_forms_shortcut(response):
    """Expose HR Forms from every DWP page without changing role-specific navigation."""
    if (
        response.status_code != 200
        or not response.content_type.startswith("text/html")
        or not session.get("user_id")
        or request.endpoint in {
            "dwp.hr_forms_home",
            "dwp.hr_time_off_new",
            "dwp.hr_pay_change_new",
            "dwp.hr_form_detail",
        }
    ):
        return response

    html = response.get_data(as_text=True)
    shortcut_url = url_for("dwp.hr_forms_home")
    shortcut = f"""
<style>
.hr-forms-shortcut {{
    position: fixed;
    right: 18px;
    bottom: 88px;
    z-index: 900;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 11px 16px;
    border-radius: 999px;
    background: #111827;
    color: #fff !important;
    text-decoration: none;
    font-weight: 950;
    box-shadow: 0 14px 30px rgba(15, 23, 42, .28);
}}
.hr-forms-shortcut:hover {{ background: #2563eb; }}
</style>
<a class="hr-forms-shortcut" href="{shortcut_url}">HR Forms</a>
"""
    if "</body>" in html and "hr-forms-shortcut" not in html:
        html = html.replace("</body>", shortcut + "\n</body>", 1)
        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())
    return response


from app.dwp import routes  # noqa: E402,F401
from app.dwp import email_delivery_fix  # noqa: E402,F401
from app.dwp import hr_forms  # noqa: E402,F401
from app.dwp import hr_forms_enhancements  # noqa: E402,F401
from app.dwp import hr_forms_pdf_email  # noqa: E402,F401
from app.dwp import separation_notice  # noqa: E402,F401
from app.dwp import separation_offboarding  # noqa: E402,F401
from app.dwp import connect_unified_documents  # noqa: E402,F401
from app.dwp import connect_unified_documents_events_fix  # noqa: E402,F401
from app.dwp import pay_change_state_variants  # noqa: E402,F401
from app.dwp import pay_change_ack_flow  # noqa: E402,F401
from app.dwp import connect_duplicate_identity_fix  # noqa: E402,F401
from app.dwp import team_member_hr_forms_file  # noqa: E402,F401
from app.dwp import payroll_workspace  # noqa: E402,F401
