import os
import requests

from flask import Blueprint, render_template, session, redirect, url_for, flash


connect_admin_bp = Blueprint(
    "connect_admin",
    __name__,
    url_prefix="/connect-admin",
)


def current_role():
    return session.get("user_role") or session.get("role")


def require_connect_admin_access():
    return current_role() in {"admin", "hr", "supervisor"}


def bpi_connect_headers(integration_secret):
    return {
        "Authorization": f"Bearer {integration_secret}",
        "X-BPI-Ops-Secret": integration_secret,
        "X-Integration-Secret": integration_secret,
        "X-BPI-Ops-Integration-Secret": integration_secret,
    }


def fetch_connect_users():
    api_base = os.getenv("BPI_CONNECT_API_BASE", "").strip().rstrip("/")
    integration_secret = os.getenv("BPI_CONNECT_INTEGRATION_SECRET", "").strip()

    result = {
        "api_base_configured": bool(api_base),
        "secret_configured": bool(integration_secret),
        "connected": False,
        "status_code": None,
        "error": None,
        "users": [],
        "counts": {},
    }

    if not api_base or not integration_secret:
        result["error"] = "BPI Connect integration is not configured."
        return result

    try:
        response = requests.get(
            f"{api_base}/api/integrations/bpi-ops/admin/users",
            headers=bpi_connect_headers(integration_secret),
            timeout=10,
        )

        result["status_code"] = response.status_code

        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}

        if response.ok:
            result["connected"] = True
            result["users"] = data.get("users", []) or []
            result["counts"] = data.get("counts", {}) or {}
        else:
            result["error"] = data

    except requests.RequestException as exc:
        result["error"] = str(exc)

    return result


def fetch_connect_summary():
    api_base = os.getenv("BPI_CONNECT_API_BASE", "").strip().rstrip("/")
    integration_secret = os.getenv("BPI_CONNECT_INTEGRATION_SECRET", "").strip()

    result = {
        "api_base_configured": bool(api_base),
        "secret_configured": bool(integration_secret),
        "connected": False,
        "status_code": None,
        "error": None,
        "summary": None,
    }

    if not api_base or not integration_secret:
        result["error"] = "BPI Connect integration is not configured."
        return result

    try:
        response = requests.get(
            f"{api_base}/api/integrations/bpi-ops/admin/summary",
            headers=bpi_connect_headers(integration_secret),
            timeout=8,
        )

        result["status_code"] = response.status_code

        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}

        if response.ok:
            result["connected"] = True
            result["summary"] = data
        else:
            result["error"] = data

    except requests.RequestException as exc:
        result["error"] = str(exc)

    return result


@connect_admin_bp.route("/users")
def users():
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))

    if not require_connect_admin_access():
        flash("You do not have access to BPI Connect Admin.", "danger")
        return redirect(url_for("dashboard.index"))

    users_status = fetch_connect_users()

    all_users = users_status.get("users", []) or []

    not_logged_in_users = [
        user for user in all_users
        if user.get("is_active") and not user.get("has_logged_in")
    ]

    pending_invite_users = [
        user for user in all_users
        if user.get("pending_invite")
    ]

    active_users = [
        user for user in all_users
        if user.get("is_active")
    ]

    inactive_users = [
        user for user in all_users
        if not user.get("is_active")
    ]

    return render_template(
        "connect_admin/users.html",
        users_status=users_status,
        all_users=all_users,
        not_logged_in_users=not_logged_in_users,
        pending_invite_users=pending_invite_users,
        active_users=active_users,
        inactive_users=inactive_users,
    )


@connect_admin_bp.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))

    if not require_connect_admin_access():
        flash("You do not have access to BPI Connect Admin.", "danger")
        return redirect(url_for("dashboard.index"))

    connect_status = fetch_connect_summary()

    return render_template(
        "connect_admin/index.html",
        connect_status=connect_status,
    )
