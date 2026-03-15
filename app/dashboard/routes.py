from collections import defaultdict
from datetime import date, timedelta, datetime
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

    opening_not_started = []
    opening_under_80 = []
    opening_complete = []
    low_integrity_stores = []

    area_groups = defaultdict(list)

    for store in stores:
        daily = DailyChecklist.query.filter_by(
            store_number=store.store_number
        ).order_by(DailyChecklist.checklist_date.desc()).first()

        checklist_percent = daily.percent_complete if daily else 0.0
        integrity_score = daily.integrity_score if daily else 0.0
        status = daily.status if daily else "not_started"

        opening_percent = calculate_section_percent(daily, "Before Open / Before 10:30")

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
            "status": status,
        }

        area_groups[store.area_name].append(store_payload)

        if opening_percent == 0:
            opening_not_started.append(store.store_number)
        elif opening_percent < 80:
            opening_under_80.append({
                "store_number": store.store_number,
                "opening_percent": opening_percent,
                "integrity_score": integrity_score,
            })
        elif opening_percent >= 100:
            opening_complete.append(store.store_number)

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

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    yesterday = today - timedelta(days=1)

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
            WeeklyFocusItem.store_number.in_(visible_store_numbers),
            WeeklyFocusItem.is_completed == False
        ).order_by(WeeklyFocusItem.item_type.asc(), WeeklyFocusItem.id.asc()).all()

        manager_weekly_focus = [
            {
                "id": item.id,
                "item_type": item.item_type,
                "item_text": item.item_text,
                "store_number": item.store_number,
            }
            for item in focus_items
        ]

    yesterday_exceptions = []
    if user_role in ["admin", "supervisor"] and visible_store_numbers:
        exception_rows = ChecklistException.query.filter(
            ChecklistException.store_number.in_(visible_store_numbers),
            ChecklistException.checklist_date == yesterday
        ).order_by(ChecklistException.store_number.asc()).all()

        yesterday_daily_map = {
            daily.store_number: daily
            for daily in DailyChecklist.query.filter(
                DailyChecklist.store_number.in_(visible_store_numbers),
                DailyChecklist.checklist_date == yesterday
            ).all()
        }

        yesterday_exceptions = []
        for row in exception_rows:
            yesterday_daily = yesterday_daily_map.get(row.store_number)
            manager_walk_percent = calculate_section_percent(yesterday_daily, "Manager's Walk")

            yesterday_exceptions.append({
                "store_number": row.store_number,
                "manager_on_duty": row.manager_on_duty or "—",
                "percent_complete": row.percent_complete,
                "integrity_score": row.integrity_score,
                "manager_walk_percent": manager_walk_percent,
                "manager_walk_missed": row.manager_walk_missed,
                "incomplete_task_count": row.incomplete_task_count,
                "incomplete_task_names": row.incomplete_task_names or "",
                "checklist_started": row.checklist_started,
                "checklist_completed": row.checklist_completed,
                "checklist_date": row.checklist_date.strftime("%Y-%m-%d"),
            })

    alerts = []

    for store_number in opening_not_started[:5]:
        alerts.append(f"Store {store_number}: opening section not started")

    for item in opening_under_80[:5]:
        alerts.append(
            f"Store {item['store_number']}: opening only {item['opening_percent']}%"
        )

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

    morning_exceptions = {
        "opening_not_started": opening_not_started,
        "opening_under_80": opening_under_80,
        "opening_complete": opening_complete,
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
        "morning_exceptions": morning_exceptions,
        "svr_completed_count": svr_completed_count,
        "svr_missing_stores": svr_missing_stores,
        "open_maintenance_count": open_maintenance_count,
        "complete_maintenance_count": complete_maintenance_count,
        "manager_weekly_focus": manager_weekly_focus,
        "yesterday_exceptions": yesterday_exceptions,
        "yesterday_label": yesterday.strftime("%B %d, %Y"),
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
        morning_exceptions=data["morning_exceptions"],
        svr_completed_count=data["svr_completed_count"],
        svr_missing_stores=data["svr_missing_stores"],
        open_maintenance_count=data["open_maintenance_count"],
        complete_maintenance_count=data["complete_maintenance_count"],
        manager_weekly_focus=data["manager_weekly_focus"],
        yesterday_exceptions=data["yesterday_exceptions"],
        yesterday_label=data["yesterday_label"],
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

    search = request.args.get("search", "").strip().lower()
    status_filter = request.args.get("status", "").strip().lower()
    type_filter = request.args.get("item_type", "").strip().lower()
    store_filter = request.args.get("store_number", "").strip()
    sort_by = request.args.get("sort", "status_store").strip().lower()

    items = WeeklyFocusItem.query.filter(
        WeeklyFocusItem.source_type == "svr",
        WeeklyFocusItem.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    filtered_items = []

    for item in items:
        if status_filter == "open" and item.is_completed:
            continue
        if status_filter == "completed" and not item.is_completed:
            continue
        if type_filter and item.item_type != type_filter:
            continue
        if store_filter and item.store_number != store_filter:
            continue

        if search:
            haystack = " ".join([
                item.store_number or "",
                item.item_type or "",
                item.item_text or "",
            ]).lower()
            if search not in haystack:
                continue

        filtered_items.append(item)

    if sort_by == "store":
        filtered_items = sorted(
            filtered_items,
            key=lambda x: (x.store_number, x.item_type, x.created_at or datetime.min)
        )
    elif sort_by == "type":
        filtered_items = sorted(
            filtered_items,
            key=lambda x: (x.item_type, x.store_number, x.created_at or datetime.min)
        )
    elif sort_by == "newest":
        filtered_items = sorted(
            filtered_items,
            key=lambda x: x.created_at or datetime.min,
            reverse=True
        )
    elif sort_by == "oldest":
        filtered_items = sorted(
            filtered_items,
            key=lambda x: x.created_at or datetime.min
        )
    elif sort_by == "completed_recent":
        filtered_items = sorted(
            filtered_items,
            key=lambda x: (
                0 if x.is_completed else 1,
                -(x.completed_at.timestamp()) if x.completed_at else float("inf"),
                x.store_number
            )
        )
    else:
        filtered_items = sorted(
            filtered_items,
            key=lambda x: (
                0 if not x.is_completed else 1,
                x.store_number,
                x.item_type,
                x.created_at or datetime.min
            )
        )

    open_count = sum(1 for item in filtered_items if not item.is_completed)
    completed_count = sum(1 for item in filtered_items if item.is_completed)

    return render_template(
        "action_board.html",
        items=filtered_items,
        stores=visible_stores,
        search=search,
        status_filter=status_filter,
        type_filter=type_filter,
        store_filter=store_filter,
        sort_by=sort_by,
        open_count=open_count,
        completed_count=completed_count,
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

