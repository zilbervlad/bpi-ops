from collections import defaultdict
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, session, jsonify, request, redirect, url_for, flash
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    Store,
    DailyChecklist,
    SVRReport,
    MaintenanceTicket,
    WeeklyFocusItem,
    ChecklistException,
)

dashboard_bp = Blueprint("dashboard", __name__)

APP_TZ = ZoneInfo("America/New_York")


def now_et():
    return datetime.now(APP_TZ)


def today_et():
    return now_et().date()


def business_date_et():
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
        ).order_by(Store.area_name.asc(), Store.store_number.asc()).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def calculate_section_percent(daily, section_name):
    if not daily:
        return 0.0

    section_items = [item for item in daily.items if item.section_name == section_name]
    total = len(section_items)

    if total == 0:
        return 0.0

    completed = sum(1 for item in section_items if item.is_completed)
    return round((completed / total) * 100, 1)


def build_dashboard_data():
    stores = get_visible_stores()
    visible_store_numbers = {store.store_number for store in stores}
    user_role = session.get("user_role")

    total_stores = len(stores)
    completed_today = 0
    in_progress_today = 0
    not_started_today = 0
    flagged_stores = 0

    low_integrity_stores = []

    opening_progress = []
    restock_progress = []
    manager_walk_progress = []
    area_groups = defaultdict(list)

    today = business_date_et()

    daily_rows = DailyChecklist.query.filter(
        DailyChecklist.checklist_date == today,
        DailyChecklist.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    daily_map = {
        row.store_number: row
        for row in daily_rows
    }

    for store in stores:
        daily = daily_map.get(store.store_number)

        checklist_percent = daily.percent_complete if daily else 0.0
        integrity_score = daily.integrity_score if daily else 0.0
        status = daily.status if daily else "not_started"

        opening_percent = calculate_section_percent(daily, "Before Open / Before 10:30")
        restock_percent = calculate_section_percent(daily, "3-O'Clock Restock")
        manager_walk_percent = calculate_section_percent(daily, "Manager's Walk")

        if status == "completed":
            completed_today += 1
        elif status == "in_progress":
            in_progress_today += 1
        else:
            not_started_today += 1

        if integrity_score > 0 and integrity_score < 80:
            flagged_stores += 1
            low_integrity_stores.append({
                "store_number": store.store_number,
                "integrity_score": integrity_score,
            })

        store_payload = {
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "checklist_percent": checklist_percent,
            "integrity_score": integrity_score,
            "opening_percent": opening_percent,
            "restock_percent": restock_percent,
            "manager_walk_percent": manager_walk_percent,
            "status": status,
        }

        area_groups[store.area_name].append(store_payload)

        opening_progress.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "area_name": store.area_name,
            "percent": opening_percent,
        })

        restock_progress.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "area_name": store.area_name,
            "percent": restock_percent,
        })

        manager_walk_progress.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "area_name": store.area_name,
            "percent": manager_walk_percent,
        })

    opening_progress = sorted(
        opening_progress,
        key=lambda x: (x["percent"], x["store_number"])
    )
    restock_progress = sorted(
        restock_progress,
        key=lambda x: (x["percent"], x["store_number"])
    )
    manager_walk_progress = sorted(
        manager_walk_progress,
        key=lambda x: (x["percent"], x["store_number"])
    )

    ordered_area_groups = dict(sorted(area_groups.items(), key=lambda x: x[0]))

    area_summaries = {}

    for area_name, area_stores in ordered_area_groups.items():
        store_count = len(area_stores)

        avg_completion = round(
            sum(s["checklist_percent"] for s in area_stores) / store_count, 1
        ) if store_count else 0.0

        integrity_values = [s["integrity_score"] for s in area_stores if s["integrity_score"] > 0]
        avg_integrity = round(
            sum(integrity_values) / len(integrity_values), 1
        ) if integrity_values else 0.0

        avg_opening = round(
            sum(s["opening_percent"] for s in area_stores) / store_count, 1
        ) if store_count else 0.0

        area_summaries[area_name] = {
            "store_count": store_count,
            "avg_completion": avg_completion,
            "avg_integrity": avg_integrity,
            "avg_opening": avg_opening,
        }

    opening_avg = round(
        sum(s["opening_percent"] for area in ordered_area_groups.values() for s in area) / total_stores, 1
    ) if total_stores else 0.0

    week_start = today - timedelta(days=today.weekday())

    weekly_svr_reports = SVRReport.query.filter(
        SVRReport.visit_date >= week_start
    ).all()

    weekly_svr_store_numbers = {
        report.store_number
        for report in weekly_svr_reports
        if report.store_number in visible_store_numbers
    }

    svr_completed_count = len(weekly_svr_store_numbers)
    svr_missing_stores = sorted(list(visible_store_numbers - weekly_svr_store_numbers))
    svr_compliance_percent = round((svr_completed_count / total_stores) * 100, 1) if total_stores else 0.0

    visible_tickets = MaintenanceTicket.query.filter(
        MaintenanceTicket.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    open_maintenance_count = sum(1 for t in visible_tickets if t.status != "complete")
    complete_maintenance_count = sum(1 for t in visible_tickets if t.status == "complete")

    manager_weekly_focus = []
    if user_role == "manager" and visible_store_numbers:
        focus_items = WeeklyFocusItem.query.filter(
            WeeklyFocusItem.store_number.in_(visible_store_numbers)
        ).order_by(
            WeeklyFocusItem.is_completed.asc(),
            WeeklyFocusItem.item_type.asc(),
            WeeklyFocusItem.id.asc()
        ).all()

        manager_weekly_focus = [
            {
                "id": item.id,
                "item_type": item.item_type,
                "item_text": item.item_text,
                "store_number": item.store_number,
                "is_completed": item.is_completed,
            }
            for item in focus_items
        ]

    alerts = []

    for item in opening_progress[:5]:
        if item["percent"] < 100:
            alerts.append(f"Store {item['store_number']}: opening at {item['percent']}%")

    for item in low_integrity_stores[:5]:
        alerts.append(
            f"Store {item['store_number']}: integrity score {item['integrity_score']}%"
        )

    for store_number in svr_missing_stores[:5]:
        alerts.append(f"Store {store_number}: missing SVR this week")

    if open_maintenance_count > 0:
        alerts.append(f"{open_maintenance_count} open maintenance task(s) across visible stores")

    if not alerts:
        alerts.append("No major exceptions right now")

    stats = {
        "checklist_completion": f"{round((completed_today / total_stores) * 100, 1) if total_stores else 0}%",
        "opening_completion": f"{opening_avg}%",
        "svr_compliance": f"{svr_compliance_percent}%",
        "stores_flagged": str(flagged_stores),
        "open_maintenance": str(open_maintenance_count),
    }

    return {
        "stats": stats,
        "alerts": alerts,
        "area_groups": ordered_area_groups,
        "area_summaries": area_summaries,
        "total_stores": total_stores,
        "completed_today": completed_today,
        "in_progress_today": in_progress_today,
        "not_started_today": not_started_today,
        "svr_completed_count": svr_completed_count,
        "svr_missing_stores": svr_missing_stores,
        "open_maintenance_count": open_maintenance_count,
        "complete_maintenance_count": complete_maintenance_count,
        "manager_weekly_focus": manager_weekly_focus,
        "opening_progress": opening_progress,
        "restock_progress": restock_progress,
        "manager_walk_progress": manager_walk_progress,
    }


@dashboard_bp.route("/")
@login_required
def home():
    user_role = session.get("user_role", "manager")

    data = build_dashboard_data()

    quick_actions = [
        {"label": "Open Checklist", "url": "/checklist/"},
        {"label": "Open Maintenance", "url": "/maintenance/"},
    ]

    if user_role in ["admin", "supervisor"]:
        quick_actions.append({"label": "Action Board", "url": "/action-board"})
        quick_actions.append({"label": "Open SVR", "url": "/svr/"})
        quick_actions.append({"label": "Verification", "url": "/verification/new"})

    if user_role == "admin":
        quick_actions.append({"label": "Manage Users", "url": "/users"})
        quick_actions.append({"label": "Manage Stores", "url": "/store-admin/"})
        quick_actions.append({"label": "SVR Admin", "url": "/svr/admin"})

    return render_template(
        "dashboard.html",
        stats=data["stats"],
        alerts=data["alerts"],
        quick_actions=quick_actions,
        user_name=session.get("user_name"),
        user_role=user_role,
        area_groups=data["area_groups"],
        area_summaries=data["area_summaries"],
        total_stores=data["total_stores"],
        completed_today=data["completed_today"],
        in_progress_today=data["in_progress_today"],
        not_started_today=data["not_started_today"],
        svr_completed_count=data["svr_completed_count"],
        svr_missing_stores=data["svr_missing_stores"],
        open_maintenance_count=data["open_maintenance_count"],
        complete_maintenance_count=data["complete_maintenance_count"],
        manager_weekly_focus=data["manager_weekly_focus"],
        opening_progress=data["opening_progress"],
        restock_progress=data["restock_progress"],
        manager_walk_progress=data["manager_walk_progress"],
    )


@dashboard_bp.route("/live-data")
@login_required
def live_data():
    data = build_dashboard_data()
    return jsonify(data)


@dashboard_bp.route("/complete-weekly-focus", methods=["POST"])
@login_required
def complete_weekly_focus():
    user_role = session.get("user_role")
    if user_role not in ["admin", "manager", "supervisor"]:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.get_json() or {}
    item_id = data.get("item_id")

    item = WeeklyFocusItem.query.get(item_id)
    if not item:
        return jsonify({"success": False, "error": "Item not found"}), 404

    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    if item.store_number not in visible_store_numbers:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    item.is_completed = True
    item.completed_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"success": True})


@dashboard_bp.route("/action-board")
@login_required
@role_required("admin", "supervisor")
def action_board():
    visible_stores = get_visible_stores()
    visible_store_numbers = {store.store_number for store in visible_stores}

    items = WeeklyFocusItem.query.filter(
        WeeklyFocusItem.source_type == "svr",
        WeeklyFocusItem.store_number.in_(visible_store_numbers)
    ).order_by(
        WeeklyFocusItem.store_number.asc(),
        WeeklyFocusItem.is_completed.asc(),
        WeeklyFocusItem.item_type.asc(),
        WeeklyFocusItem.created_at.asc(),
        WeeklyFocusItem.id.asc(),
    ).all() if visible_store_numbers else []

    grouped = defaultdict(lambda: {
        "open_cleaning": [],
        "open_goal": [],
        "completed_cleaning": [],
        "completed_goal": [],
    })

    for item in items:
        item_payload = {
            "id": item.id,
            "item_text": item.item_text,
            "item_type": item.item_type,
            "created_at": item.created_at,
            "completed_at": item.completed_at,
            "is_completed": item.is_completed,
            "store_number": item.store_number,
        }

        if item.is_completed:
            if item.item_type == "cleaning":
                grouped[item.store_number]["completed_cleaning"].append(item_payload)
            else:
                grouped[item.store_number]["completed_goal"].append(item_payload)
        else:
            if item.item_type == "cleaning":
                grouped[item.store_number]["open_cleaning"].append(item_payload)
            else:
                grouped[item.store_number]["open_goal"].append(item_payload)

    store_tiles = []

    for store in visible_stores:
        groups = grouped.get(store.store_number, {
            "open_cleaning": [],
            "open_goal": [],
            "completed_cleaning": [],
            "completed_goal": [],
        })

        open_total = len(groups["open_cleaning"]) + len(groups["open_goal"])
        completed_total = len(groups["completed_cleaning"]) + len(groups["completed_goal"])
        total_items = open_total + completed_total

        if total_items == 0:
            tile_class = "tile-gray"
        elif open_total == 0:
            tile_class = "tile-green"
        elif completed_total > 0:
            tile_class = "tile-yellow"
        else:
            tile_class = "tile-red"

        store_tiles.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "tile_class": tile_class,
            "open_total": open_total,
            "completed_total": completed_total,
            "open_cleaning": groups["open_cleaning"],
            "open_goal": groups["open_goal"],
            "completed_cleaning": groups["completed_cleaning"],
            "completed_goal": groups["completed_goal"],
        })

    return render_template(
        "action_board.html",
        store_tiles=store_tiles,
    )


@dashboard_bp.route("/clear-weekly-focus-items", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def clear_weekly_focus_items():
    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    item_ids = request.form.getlist("item_ids")

    if not item_ids:
        flash("No completed items selected.", "error")
        return redirect(url_for("dashboard.action_board"))

    cleared_count = 0

    items = WeeklyFocusItem.query.filter(
        WeeklyFocusItem.id.in_(item_ids),
        WeeklyFocusItem.source_type == "svr",
        WeeklyFocusItem.store_number.in_(visible_store_numbers)
    ).all()

    for item in items:
        if item.is_completed:
            db.session.delete(item)
            cleared_count += 1

    db.session.commit()

    flash(f"Cleared {cleared_count} completed item(s).", "success")
    return redirect(url_for("dashboard.action_board"))