import re

from flask import abort, render_template, request, session, url_for

from app import db
from app.dwp import dwp_bp
from app.dwp import routes as dwp_routes
from app.models import User


PAYROLL_ROLES = {"payroll"}


def _current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _is_payroll(user=None):
    """Recognize payroll from either the database role or any active session role."""
    user = user or _current_user()
    roles = {
        str(value).strip().lower()
        for value in [
            getattr(user, "role", None) if user else None,
            session.get("account_role"),
            session.get("user_role"),
            session.get("access_role"),
            session.get("role"),
            session.get("role_label"),
        ]
        if value
    }
    return bool(roles & PAYROLL_ROLES) or any("payroll" in role for role in roles)


@dwp_bp.route("/team-members")
def payroll_team_members():
    user = _current_user()
    if not user or not (_is_payroll(user) or dwp_routes.is_admin_like(user)):
        abort(403)

    search = (request.args.get("search") or "").strip()
    query = User.query.filter(User.is_active.is_(True))

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                User.name.ilike(like),
                User.username.ilike(like),
                User.email.ilike(like),
                User.store_number.ilike(like),
                User.position.ilike(like),
            )
        )

    employees = (
        query
        .filter(~User.role.in_(["admin", "hr", "payroll"]))
        .order_by(User.store_number.asc(), User.name.asc())
        .limit(800)
        .all()
    )

    return render_template(
        "team_members/payroll_directory.html",
        employees=employees,
        search=search,
        user=user,
    )


# Payroll needs company-wide TM files. Keep the existing helper name so the
# existing team_member_file route, DWP index, and access checks resolve it.
_original_is_admin_like = dwp_routes.is_admin_like


def _admin_hr_or_payroll(user):
    return _original_is_admin_like(user) or _is_payroll(user)


dwp_routes.is_admin_like = _admin_hr_or_payroll


@dwp_bp.after_request
def build_payroll_dashboard(response):
    if (
        response.status_code != 200
        or not response.content_type.startswith("text/html")
        or not _is_payroll()
        or request.path not in {"/", "/dashboard", "/dashboard/"}
    ):
        return response

    html = response.get_data(as_text=True)

    html = html.replace("LIVE OPERATIONS", "PAYROLL WORKSPACE", 1)
    html = html.replace("BPI Ops Command Center", "BPI Ops Payroll Dashboard", 1)
    html = html.replace(
        "Real-time checklist execution, opening integrity, SVR compliance, maintenance visibility, and shift execution progress.",
        "Review acknowledged payroll forms, open team member files, and process employee records from one place.",
        1,
    )
    html = html.replace("Live Dashboard", "Payroll Dashboard", 1)

    payroll_panel = f'''
<section class="panel premium-panel quick-actions-panel payroll-workspace-panel">
    <div class="quick-actions-head">
        <div>
            <h3>Payroll Workspace</h3>
            <div class="area-subtitle">Forms and employee records ready for payroll review.</div>
        </div>
        <span class="summary-pill">Payroll</span>
    </div>

    <div class="quick-action-grid">
        <a class="quick-action-tile" href="{url_for('dwp.hr_forms_home')}">
            <span>Payroll Forms</span><span class="quick-action-arrow">→</span>
        </a>
        <a class="quick-action-tile" href="{url_for('dwp.payroll_team_members')}">
            <span>Team Member Files</span><span class="quick-action-arrow">→</span>
        </a>
        <a class="quick-action-tile" href="{url_for('hr_documents.index')}">
            <span>HR Documents</span><span class="quick-action-arrow">→</span>
        </a>
        <a class="quick-action-tile" href="{url_for('dwp.index')}">
            <span>DWP Records</span><span class="quick-action-arrow">→</span>
        </a>
    </div>
</section>
'''

    quick_actions_pattern = re.compile(
        r'<section class="panel premium-panel quick-actions-panel">.*?</section>',
        re.DOTALL,
    )
    html = quick_actions_pattern.sub(payroll_panel, html, count=1)

    response.set_data(html)
    response.headers["Content-Length"] = len(response.get_data())
    return response
