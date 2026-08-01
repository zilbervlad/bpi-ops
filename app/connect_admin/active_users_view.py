from flask import flash, redirect, render_template, request, session, url_for

from app.models import Store
from app.auth.routes import VALID_ROLES

from .routes import (
    can_manage_connect_users,
    fetch_connect_users,
    require_connect_admin_access,
)


def users_active_only():
    """Show active Connect users by default and isolate deactivated users."""
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))

    if not require_connect_admin_access():
        flash("You do not have access to BPI Connect Admin.", "danger")
        return redirect(url_for("dashboard.index"))

    users_status = fetch_connect_users()
    all_users = users_status.get("users", []) or []

    selected_store = (request.args.get("store") or "").strip().lower()
    selected_status = (request.args.get("status") or "active").strip().lower()
    selected_role = (request.args.get("role") or "").strip().lower()
    search_query = (request.args.get("q") or "").strip().lower()

    valid_statuses = {
        "active",
        "not-logged-in",
        "pending",
        "logged-in",
        "inactive",
    }
    if selected_status not in valid_statuses:
        selected_status = "active"

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

        if selected_status == "active" and not user.get("is_active"):
            return False

        if selected_status != "active" and status_value != selected_status:
            return False

        if selected_role and role_value != selected_role:
            return False

        if search_query and search_query not in haystack:
            return False

        return True

    filtered_users = [user for user in all_users if matches_filters(user)]

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

    local_store_options = (
        Store.query
        .filter_by(is_active=True)
        .order_by(Store.store_number.asc())
        .all()
    )
    manageable_roles = sorted(role for role in VALID_ROLES if role != "admin")

    store_rollup_map = {}
    for user in all_users:
        # Deactivated users do not belong in active rollout totals.
        if not user.get("is_active"):
            continue

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
        row["active"] += 1

        if user.get("has_logged_in"):
            row["logged_in"] += 1
        else:
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
        local_store_options=local_store_options,
        manageable_roles=manageable_roles,
        can_manage_users=can_manage_connect_users(),
        store_rollup=store_rollup,
    )


def install(connect_admin_bp):
    connect_admin_bp.view_functions["users"] = users_active_only
