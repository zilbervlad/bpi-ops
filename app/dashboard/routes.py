from collections import defaultdict
from datetime import date, timedelta
from flask import Blueprint, render_template, session, jsonify
from app.auth.routes import login_required
from app.models import Store, DailyChecklist, SVRReport, MaintenanceTicket

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


def build_dashboard_data():
    stores = get_visible_stores()
    visible_store_numbers = {store.store_number for store in stores}

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

        opening_total = 0
        opening_completed = 0

        if daily:
            for item in daily.items:
                if item.section_name == "Before Open / Before 10:30":
                    opening_total += 1
                    if item.is_completed:
                        opening_completed += 1

        opening_percent = round((opening_completed / opening_total) * 100, 1) if opening_total else 0.0

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

    # -----------------------------
    # SVR compliance (this week)
    # -----------------------------
    today = date.today()
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

    # -----------------------------
    # Maintenance summary
    # -----------------------------
    visible_tickets = MaintenanceTicket.query.filter(
        MaintenanceTicket.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    open_maintenance_count = sum(1 for t in visible_tickets if t.status != "complete")
    complete_maintenance_count = sum(1 for t in visible_tickets if t.status == "complete")

    # -----------------------------
    # Real alerts
    # -----------------------------
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
    )


@dashboard_bp.route("/live-data")
@login_required
def live_data():
    data = build_dashboard_data()
    return jsonify(data)