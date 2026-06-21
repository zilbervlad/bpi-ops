import os
import requests

from flask import Blueprint, render_template, session, redirect, url_for, flash, request


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

    selected_store = (request.args.get("store") or "").strip().lower()
    selected_status = (request.args.get("status") or "").strip().lower()
    selected_role = (request.args.get("role") or "").strip().lower()
    search_query = (request.args.get("q") or "").strip().lower()

    def user_status(user):
        if not user.get("is_active"):
            return "inactive"
        if user.get("pending_invite"):
            return "pending"
        if user.get("has_logged_in"):
            return "logged-in"
        return "not-logged-in"

    def matches_filters(user):
        store_value = str(user.get("store_number") or "no-store").strip().lower()
        role_value = str(user.get("role") or "unknown").strip().lower()
        status_value = user_status(user)

        haystack = " ".join([
            str(user.get("name") or ""),
            str(user.get("email") or ""),
            str(user.get("phone_number") or ""),
        ]).lower()

        if selected_store and store_value != selected_store:
            return False

        if selected_status and status_value != selected_status:
            return False

        if selected_role and role_value != selected_role:
            return False

        if search_query and search_query not in haystack:
            return False

        return True

    filtered_users = [
        user for user in all_users
        if matches_filters(user)
    ]

    not_logged_in_users = [
        user for user in filtered_users
        if user.get("is_active") and not user.get("has_logged_in")
    ]

    pending_invite_users = [
        user for user in filtered_users
        if user.get("pending_invite")
    ]

    active_users = [
        user for user in filtered_users
        if user.get("is_active")
    ]

    inactive_users = [
        user for user in filtered_users
        if not user.get("is_active")
    ]

    role_options = sorted({
        str(user.get("role") or "").strip()
        for user in all_users
        if str(user.get("role") or "").strip()
    })

    store_options = sorted({
        str(user.get("store_number") or "").strip()
        for user in all_users
        if str(user.get("store_number") or "").strip()
    })

    store_rollup_map = {}

    for user in all_users:
        store_number = str(user.get("store_number") or "").strip() or "No Store"
        store_name = str(user.get("store_name") or "").strip()
        area = str(user.get("area") or "").strip()

        if store_number not in store_rollup_map:
            store_rollup_map[store_number] = {
                "store_number": store_number,
                "store_name": store_name,
                "area": area,
                "total": 0,
                "active": 0,
                "inactive": 0,
                "logged_in": 0,
                "not_logged_in": 0,
                "pending_invites": 0,
                "push_tokens": 0,
            }

        row = store_rollup_map[store_number]
        row["total"] += 1

        if user.get("is_active"):
            row["active"] += 1
        else:
            row["inactive"] += 1

        if user.get("has_logged_in"):
            row["logged_in"] += 1
        elif user.get("is_active"):
            row["not_logged_in"] += 1

        if user.get("pending_invite"):
            row["pending_invites"] += 1

        try:
            row["push_tokens"] += int(user.get("active_push_tokens") or 0)
        except (TypeError, ValueError):
            pass

    store_rollup = sorted(
        store_rollup_map.values(),
        key=lambda row: (-row["not_logged_in"], str(row["store_number"])),
    )

    return render_template(
        "connect_admin/users.html",
        users_status=users_status,
        all_users=all_users,
        filtered_users=filtered_users,
        selected_store=selected_store,
        selected_status=selected_status,
        selected_role=selected_role,
        search_query=search_query,
        not_logged_in_users=not_logged_in_users,
        pending_invite_users=pending_invite_users,
        active_users=active_users,
        inactive_users=inactive_users,
        role_options=role_options,
        store_options=store_options,
        store_rollup=store_rollup,
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
