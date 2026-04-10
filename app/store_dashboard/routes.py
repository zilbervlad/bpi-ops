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

    items = daily.items or []
    section_items = [item for item in items if item.section_name == section_name]
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


def get_active_section_config():
    now = now_et()

    if now.hour < 11:
        return {
            "section_name": "Before Open / Before 10:30",
            "label": "Opening",
        }
    elif now.hour < 22:
        return {
            "section_name": "3-O'Clock Restock",
            "label": "3-O'Clock Restock",
        }
    else:
        return {
            "section_name": "Manager's Walk",
            "label": "Manager's Walk",
        }


def get_tile_display_from_stats(stats):
    percent = int(stats.get("percent", 0))
    status_text = stats.get("status_text", "Not Started")

    if status_text == "Complete":
        tile_class = "tile-green"
        status_label = "Completed"
        status = "completed"
    elif status_text == "In Progress":
        tile_class = "tile-red"
        status_label = "In Progress"
        status = "in_progress"
    else:
        tile_class = "tile-gray"
        status_label = "Not Started"
        status = "not_started"

    return {
        "percent": percent,
        "status": status,
        "status_label": status_label,
        "tile_class": tile_class,
    }


def build_heat_map(today, active_section_name):
    all_stores = Store.query.filter_by(is_active=True).order_by(
        Store.area_name.asc(),
        Store.store_number.asc()
    ).all()

    all_daily = DailyChecklist.query.filter_by(
        checklist_date=today
    ).all()

    daily_by_store = {row.store_number: row for row in all_daily}

    heat_map = []

    for store in all_stores:
        daily = daily_by_store.get(store.store_number)
        active_stats = calculate_section_stats(daily, active_section_name)
        tile_display = get_tile_display_from_stats(active_stats)

        heat_map.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "percent": tile_display["percent"],
            "status": tile_display["status"],
            "status_label": tile_display["status_label"],
            "tile_class": tile_display["tile_class"],
        })

    return heat_map, daily_by_store


@store_dashboard_bp.route("/")
@login_required
def index():
    role = session.get("user_role")
    visible_stores = get_visible_stores()

    if not visible_stores:
        abort(403)

    if role == "manager":
        user_store = session.get("user_store")
        if not user_store:
            abort(403)
        return redirect(url_for("store_dashboard.detail", store_number=user_store))

    return redirect(
        url_for("store_dashboard.detail", store_number=visible_stores[0].store_number)
    )


@store_dashboard_bp.route("/<store_number>")
@login_required
def detail(store_number):
    if not user_can_access(store_number):
        abort(403)

    today = business_date()
    active_config = get_active_section_config()
    active_section_name = active_config["section_name"]
    active_label = active_config["label"]

    visible_stores = get_visible_stores()
    selected_store = Store.query.filter_by(
        store_number=store_number,
        is_active=True
    ).first_or_404()

    heat_map, daily_by_store = build_heat_map(today, active_section_name)
    selected_daily = daily_by_store.get(store_number)

    manager_name = "Not Set"
    if selected_daily:
        manager_name = (
            selected_daily.manager_on_duty
            or selected_daily.opening_manager
            or selected_daily.closing_manager
            or "Not Set"
        )

    open_focus_items = WeeklyFocusItem.query.filter_by(
        store_number=store_number,
        is_completed=False
    ).order_by(
        WeeklyFocusItem.created_at.asc(),
        WeeklyFocusItem.id.asc()
    ).all()

    cleaning_items = [item for item in open_focus_items if item.item_type == "cleaning"]
    goal_items = [item for item in open_focus_items if item.item_type == "goal"]

    before_open_stats = calculate_section_stats(selected_daily, "Before Open / Before 10:30")
    restock_stats = calculate_section_stats(selected_daily, "3-O'Clock Restock")
    manager_walk_stats = calculate_section_stats(selected_daily, "Manager's Walk")

    active_stats = calculate_section_stats(selected_daily, active_section_name)
    active_display = get_tile_display_from_stats(active_stats)

    overall_completion = active_display["percent"]
    checklist_status_label = active_display["status_label"]

    return render_template(
        "store_dashboard/index.html",
        today=today,
        visible_stores=visible_stores,
        selected_store=selected_store,
        overall_completion=overall_completion,
        checklist_status_label=checklist_status_label,
        active_label=active_label,
        manager_name=manager_name,
        cleaning_items=cleaning_items,
        goal_items=goal_items,
        before_open_stats=before_open_stats,
        restock_stats=restock_stats,
        manager_walk_stats=manager_walk_stats,
        heat_map=heat_map,
        is_manager=(session.get("user_role") == "manager"),
    )