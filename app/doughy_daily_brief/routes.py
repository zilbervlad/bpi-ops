import hmac
import os

from flask import jsonify, request

from app.doughy_daily_brief import doughy_daily_brief_bp
from app.services.doughy_daily_brief import send_daily_briefs


def configured_secret():
    return (
        os.getenv("DOUGHY_DAILY_BRIEF_SECRET")
        or os.getenv("CRON_SECRET")
        or ""
    ).strip()


def supplied_secret():
    authorization = (
        request.headers.get("Authorization")
        or ""
    ).strip()

    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    return (
        request.headers.get("X-Cron-Secret")
        or request.args.get("secret")
        or ""
    ).strip()


def authorized():
    expected = configured_secret()
    supplied = supplied_secret()

    return bool(
        expected
        and supplied
        and hmac.compare_digest(expected, supplied)
    )


@doughy_daily_brief_bp.route("", methods=["POST"])
def run_daily_brief():
    if not authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized",
        }), 401

    payload = request.get_json(silent=True) or {}

    force = bool(payload.get("force"))
    test_email = (
        str(payload.get("test_email") or "").strip()
        or None
    )

    result = send_daily_briefs(
        force=force,
        test_email=test_email,
    )

    status_code = 200 if not result["failed_count"] else 207
    return jsonify(result), status_code
