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
    if "cash" in path:
        return "cash"
    if "connect" in path:
        return "connect_admin"
    if "dwp" in path:
        return "dwp"

    return "unknown"


def _safe_attr(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def _extract_context_from_path(path):
    clean_path = (path or "").split("?")[0]
    parts = [part for part in clean_path.split("/") if part]

    context = {
        "path": clean_path or "/",
        "section": parts[0] if parts else "dashboard",
        "resource_id": None,
        "store_from_path": None,
    }

    for part in parts:
        if part.isdigit():
            context["resource_id"] = part
            break

    for part in parts:
        if part.isdigit() and len(part) == 4:
            context["store_from_path"] = part
            break

    return context


@doughy_bp.route("/context")
@login_required
def context():
    page_path = request.args.get("path") or request.referrer or request.path
    page = _guess_page_from_path(page_path)
    path_context = _extract_context_from_path(page_path)

    user = _current_user()

    role = _safe_attr(user, "role", None)
    store = (
        request.args.get("store")
        or path_context.get("store_from_path")
        or session.get("user_store")
        or _safe_attr(user, "store_number", None)
        or _safe_attr(user, "store", None)
        or _safe_attr(user, "primary_store", None)
    )

    company_id = (
        session.get("company_id")
        or session.get("current_company_id")
        or _safe_attr(user, "company_id", None)
        or _safe_attr(user, "current_company_id", None)
    )

    return jsonify(
        {
            "page": page,
            "path": path_context.get("path"),
            "section": path_context.get("section"),
            "resource_id": path_context.get("resource_id"),
            "role": role,
            "store": str(store) if store else None,
            "company_id": company_id,
            "mode": "read_only_context",
        }
    )
