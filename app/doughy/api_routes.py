import hmac
import os
import re

from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from app.services.doughy_universal_gateway import build_doughy_universal_context


doughy_api_bp = Blueprint(
    "doughy_api",
    __name__,
    url_prefix="/api/integrations/doughy",
)


_MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _requested_date_from_text(value: str):
    """
    Extract an obvious requested business date from a Doughy question.

    Explicit API date values still take priority. This is only a fallback
    for natural-language questions such as "How did we do July 14?"
    """
    text = str(value or "").strip().lower()

    if not text:
        return None

    today = date.today()

    if re.search(r"\b(yesterday|last night)\b", text):
        return (today - timedelta(days=1)).isoformat()

    month_match = re.search(
        r"\b("
        + "|".join(_MONTH_NUMBERS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?"
          r"(?:,?\s+(\d{4}))?\b",
        text,
    )

    if month_match:
        month = _MONTH_NUMBERS[month_match.group(1)]
        day = int(month_match.group(2))
        year = int(month_match.group(3) or today.year)

        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    numeric_match = re.search(
        r"(?<!\d)(\d{1,2})[/-](\d{1,2})"
        r"(?:[/-](\d{2}|\d{4}))?(?!\d)",
        text,
    )

    if numeric_match:
        month = int(numeric_match.group(1))
        day = int(numeric_match.group(2))
        raw_year = numeric_match.group(3)

        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        else:
            year = today.year

        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    return None


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

    incoming_query = (
        payload.get("query")
        or payload.get("question")
        or request.args.get("query")
        or request.args.get("question")
        or ""
    )

    requested_date = (
        payload.get("date")
        or request.args.get("date")
        or _requested_date_from_text(incoming_query)
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
        "users",
        "dwp",
        "hr_documents",
        "forms",
        "prep",
        "checklist_history",
        "maintenance_history",
        "maintenance_schedule",
        "svr_history",
        "verification_history",
        "nightly_history",
        "cash_history",
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

    # Read-only requester scope supplied by an approved integration.
    #
    # The integration key authenticates the calling service.
    # The requester fields limit which stores the data gateway may return.
    requester_role = str(
        payload.get("requesting_role")
        or request.args.get("requesting_role")
        or ""
    ).strip().lower()

    requester_area = str(
        payload.get("requesting_area")
        or request.args.get("requesting_area")
        or ""
    ).strip() or None

    requester_store = str(
        payload.get("requesting_store")
        or request.args.get("requesting_store")
        or ""
    ).strip() or None

    # Preserve the existing standalone-admin behavior for older trusted
    # integrations that have not started sending requester scope yet.
    if not requester_role:
        requester_role = "admin"

    user_context = {
        "role": requester_role,
        "user_area": requester_area,
        "user_store": requester_store,
    }

    context = build_doughy_universal_context(
        user_context=user_context,
        page_context=page_context,
        requested_store=requested_store,
        requested_date=requested_date,
        date_from=(
            payload.get("date_from")
            or request.args.get("date_from")
        ),
        date_to=(
            payload.get("date_to")
            or request.args.get("date_to")
        ),
        status=(
            payload.get("status")
            or request.args.get("status")
            or ""
        ),
        employee=(
            payload.get("employee")
            or request.args.get("employee")
            or ""
        ),
        query_text=incoming_query,
        limit=(
            payload.get("limit")
            or request.args.get("limit")
            or 100
        ),
    )

    status_code = (
        200
        if context.get("ok")
        else 403
    )

    return jsonify(context), status_code
