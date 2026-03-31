from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import render_template, session, redirect, url_for, abort

from app.store_dashboard import store_dashboard_bp
from app.auth.routes import login_required
from app.models import Store, DailyChecklist, WeeklyFocusItem

APP_TZ = ZoneInfo("America/New_York")


def now_et():
    return datetime.now(APP_TZ)


def business_date():
    now = now_et()
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(
            Store.area_name.asc(),
            Store.store_number.asc()
        ).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(
            Store.area_name.asc(),
            Store.store_number.asc()
        ).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def user_can_access(store_number):
    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    return store_number in visible_store_numbers


def calculate_section_stats(daily, section_name):
    if not daily:
        return {
            "completed": 0,
            "total": 0,
            "percent": 0,
            "status_text": "Not Started",
        }

    section_items = [item for item in daily.items if item.section_name == section_name]
    total = len(section_items)

    if total == 0:
        return {
            "completed": 0,
            "total": 0,
            "percent": 0,
            "status_text": "Not Started",
        }

    completed = sum(1 for item in section_items if item.is_completed)
    percent = round((completed / total) * 100) if total else 0

    if completed == 0:
        status_text = "Not Started"
    elif completed == total:
        status_text = "Complete"
    else:
        status_text = "In Progress"

    return {
        "completed": completed,
        "total": total,
        "percent": percent,
        "status_text": status_text,
    }


def build_heat_map(today):
    stores = Store.query.filter_by(is_active=True).order_by(
        Store.area_name.asc(),
        Store.store_number.asc()
    ).all()

    heat_map = []

    for store in stores:
        daily = DailyChecklist.query.filter_by(
            store_number=store.store_number,
            checklist_date=today
        ).first()

        percent = int(round(daily.percent_complete)) if daily else 0
        status = daily.status if daily else "not_started"

        if status == "completed":
            tile_class = "tile-green"
            status_label = "Completed"
        elif status == "in_progress":
            tile_class = "tile-red"
            status_label = "In Progress"
        else:
            tile_class = "tile-gray"
            status_label = "Not Started"

        heat_map.append({
            "store_number": store.store_number,
            "percent": percent,
            "status": status,
            "status_label": status_label,
            "tile_class": tile_class,
        })

    return heat_map


@store_dashboard_bp.route("/")
@login_required
def index():
    role = session.get("user_role")
    visible_stores = get_visible_stores()

    if not visible_stores:
        abort(403)

    if role == "manager":
        return redirect(
            url_for("store_dashboard.detail", store_number=session.get("user_store"))
        )

    selected_store = visible_stores[0]
    return redirect(
        url_for("store_dashboard.detail", store_number=selected_store.store_number)
    )


@store_dashboard_bp.route("/<store_number>")
@login_required
def detail(store_number):
    if not user_can_access(store_number):
        abort(403)

    today = business_date()

    visible_stores = get_visible_stores()
    selected_store = Store.query.filter_by(
        store_number=store_number,
        is_active=True
    ).first_or_404()

    daily = DailyChecklist.query.filter_by(
        store_number=store_number,
        checklist_date=today
    ).first()

    overall_completion = int(round(daily.percent_complete)) if daily else 0
    checklist_status = (daily.status or "not_started") if daily else "not_started"
    manager_name = None

    if daily:
        manager_name = daily.manager_on_duty or daily.opening_manager or daily.closing_manager

    if checklist_status == "completed":
        checklist_status_label = "Completed"
    elif checklist_status == "in_progress":
        checklist_status_label = "In Progress"
    else:
        checklist_status_label = "Not Started"

    cleaning_items = WeeklyFocusItem.query.filter_by(
        store_number=store_number,
        item_type="cleaning",
        is_completed=False
    ).order_by(
        WeeklyFocusItem.created_at.asc(),
        WeeklyFocusItem.id.asc()
    ).all()

    goal_items = WeeklyFocusItem.query.filter_by(
        store_number=store_number,
        item_type="goal",
        is_completed=False
    ).order_by(
        WeeklyFocusItem.created_at.asc(),
        WeeklyFocusItem.id.asc()
    ).all()

    before_open_stats = calculate_section_stats(daily, "Before Open / Before 10:30")
    restock_stats = calculate_section_stats(daily, "3-O'Clock Restock")
    manager_walk_stats = calculate_section_stats(daily, "Manager's Walk")

    heat_map = build_heat_map(today)

    return render_template(
        "store_dashboard/index.html",
        today=today,
        visible_stores=visible_stores,
        selected_store=selected_store,
        overall_completion=overall_completion,
        checklist_status_label=checklist_status_label,
        manager_name=manager_name or "Not Set",
        cleaning_items=cleaning_items,
        goal_items=goal_items,
        before_open_stats=before_open_stats,
        restock_stats=restock_stats,
        manager_walk_stats=manager_walk_stats,
        heat_map=heat_map,
        is_manager=(session.get("user_role") == "manager"),
    )