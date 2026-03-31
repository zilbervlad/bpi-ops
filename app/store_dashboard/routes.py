from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import render_template, session, redirect, url_for, abort

from app.store_dashboard import store_dashboard_bp
from app.auth.routes import login_required
from app.models import (
    Store,
    DailyChecklist,
    WeeklyFocusItem,
    MaintenanceTicket,
    VerificationReport
)

APP_TZ = ZoneInfo("America/New_York")


def business_date():
    now = datetime.now(APP_TZ)
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role == "admin":
        return Store.query.filter_by(is_active=True).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).all()

    return []


def user_can_access(store_number):
    visible = {s.store_number for s in get_visible_stores()}
    return store_number in visible


@store_dashboard_bp.route("/")
@login_required
def index():
    role = session.get("user_role")

    # Managers go straight to their store
    if role == "manager":
        return redirect(url_for("store_dashboard.detail", store_number=session.get("user_store")))

    stores = get_visible_stores()

    return render_template(
        "store_dashboard/index.html",
        stores=stores,
        selected_store=None
    )


@store_dashboard_bp.route("/<store_number>")
@login_required
def detail(store_number):
    if not user_can_access(store_number):
        abort(403)

    today = business_date()

    store = Store.query.filter_by(store_number=store_number).first()

    daily = DailyChecklist.query.filter_by(
        store_number=store_number,
        checklist_date=today
    ).first()

    maintenance = MaintenanceTicket.query.filter_by(
        store_number=store_number
    ).all()

    weekly_focus = WeeklyFocusItem.query.filter_by(
        store_number=store_number
    ).order_by(WeeklyFocusItem.is_completed.asc()).all()

    latest_verification = VerificationReport.query.filter_by(
        store_number=store_number
    ).order_by(VerificationReport.created_at.desc()).first()

    return render_template(
        "store_dashboard/index.html",
        selected_store=store,
        daily=daily,
        maintenance=maintenance,
        weekly_focus=weekly_focus,
        latest_verification=latest_verification
    )