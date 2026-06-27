from flask import jsonify, request, session

from app.auth.routes import login_required
from app.models import User

from . import doughy_bp


def _guess_page_from_path(path):
    path = (path or "").lower()

    if "checklist" in path:
        return "checklist"
    if "svr" in path or "store-visit" in path:
        return "svr"
    if "maintenance" in path:
        return "maintenance"
    if "admin" in path:
        return "admin"
    if "dashboard" in path or path == "/":
        return "dashboard"
    if "nightly" in path:
        return "nightly_numbers"
    if "forms" in path:
        return "forms"
    if "verification" in path:
        return "verification"

    return "unknown"


def _safe_attr(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


@doughy_bp.route("/context")
@login_required
def context():
    page_path = request.args.get("path") or request.referrer or request.path
    page = _guess_page_from_path(page_path)

    user = _current_user()

    role = _safe_attr(user, "role", None)
    store = (
        _safe_attr(user, "store_number", None)
        or _safe_attr(user, "store", None)
        or _safe_attr(user, "primary_store", None)
    )

    company_id = (
        _safe_attr(user, "company_id", None)
        or _safe_attr(user, "current_company_id", None)
    )

    return jsonify(
        {
            "page": page,
            "role": role,
            "store": str(store) if store else None,
            "company_id": company_id,
            "mode": "read_only_context",
        }
    )
