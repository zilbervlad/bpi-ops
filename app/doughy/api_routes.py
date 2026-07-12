import hmac
import os

from flask import Blueprint, jsonify, request

from app.services.doughy_data_gateway import build_doughy_context


doughy_api_bp = Blueprint(
    "doughy_api",
    __name__,
    url_prefix="/api/integrations/doughy",
)


def _authorized() -> bool:
    expected = (
        os.getenv("DOUGHY_LIVE_CONTEXT_KEY")
        or ""
    ).strip()

    if not expected:
        return False

    auth_header = (
        request.headers.get("Authorization")
        or ""
    ).strip()

    provided = ""

    if auth_header.lower().startswith("bearer "):
        provided = auth_header.split(" ", 1)[1].strip()

    return hmac.compare_digest(
        provided,
        expected,
    )


@doughy_api_bp.route(
    "/live-context",
    methods=["GET", "POST"],
)
def live_context():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = request.get_json(
        silent=True,
    ) or {}

    requested_store = (
        payload.get("store")
        or request.args.get("store")
        or ""
    ).strip() or None

    requested_date = (
        payload.get("date")
        or request.args.get("date")
        or None
    )

    module = (
        payload.get("module")
        or request.args.get("module")
        or "dashboard"
    ).strip().lower()

    allowed_modules = {
        "all",
        "dashboard",
        "checklist",
        "maintenance",
        "svr",
        "verification",
        "nightly_numbers",
        "weekly_focus",
        "cash",
    }

    if module not in allowed_modules:
        module = "dashboard"

    page_context = {
        "page": module,
        "path": "/standalone-doughy",
        "section": (
            "dashboard"
            if module == "all"
            else module
        ),
        "resource_id": None,
        "endpoint": "doughy_api.live_context",
    }

    # Dedicated read-only service identity.
    # Admin scope allows company-wide Boston Pie visibility.
    user_context = {
        "role": "admin",
        "user_area": None,
        "user_store": None,
    }

    context = build_doughy_context(
        user_context=user_context,
        page_context=page_context,
        requested_store=requested_store,
        requested_date=requested_date,
    )

    status_code = (
        200
        if context.get("ok")
        else 403
    )

    return jsonify(context), status_code
