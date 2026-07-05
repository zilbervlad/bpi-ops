from collections import defaultdict
import json
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from flask import Blueprint, render_template, session, jsonify, request, redirect, url_for, flash
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    DWPRecord,
    Store,
    DailyChecklist,
    SVRReport,
    MaintenanceTicket,
    WeeklyFocusItem, ModuleAccessSetting,
    ChecklistException,
    CashLog,
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


def money_value(value):
    if value is None:
        return None
    return round(float(value), 2)


def build_manager_cash_summary(store_number, business_date):
    summary = {
        "business_date": business_date.strftime("%Y-%m-%d"),
        "closing_opening": {
            "status": "not_started",
            "message": "Cash not started today",
            "difference": None,
            "opening_total": None,
            "closing_total": None,
            "opening_date": None,
            "closing_date": None,
        },
        "dayshift": {
            "status": "missing_midshift",
            "message": "Dayshift cash not submitted",
            "difference": None,
            "total_cash": None,
            "amount_to_account_for": None,
            "log_date": business_date.strftime("%Y-%m-%d"),
        },
    }

    today_opening = (
        CashLog.query.filter_by(
            store_number=store_number,
            log_date=business_date,
            shift_type="opening",
        )
        .order_by(CashLog.created_at.desc(), CashLog.id.desc())
        .first()
    )

    previous_closing = (
        CashLog.query.filter(
            CashLog.store_number == store_number,
            CashLog.shift_type == "closing",
            CashLog.log_date < business_date,
        )
        .order_by(CashLog.log_date.desc(), CashLog.created_at.desc(), CashLog.id.desc())
        .first()
    )

    today_midshift = (
        CashLog.query.filter_by(
            store_number=store_number,
            log_date=business_date,
            shift_type="midshift",
        )
        .order_by(CashLog.created_at.desc(), CashLog.id.desc())
        .first()
    )

    if today_opening and previous_closing:
        opening_total = money_value(today_opening.total_cash)
        closing_total = money_value(previous_closing.total_cash)
        difference = money_value(opening_total - closing_total)

        summary["closing_opening"] = {
            "status": "balanced" if difference == 0 else "variance",
            "message": "Opening compared to prior closing",
            "difference": difference,
            "opening_total": opening_total,
            "closing_total": closing_total,
            "opening_date": today_opening.log_date.strftime("%Y-%m-%d"),
            "closing_date": previous_closing.log_date.strftime("%Y-%m-%d"),
        }
    elif today_opening and not previous_closing:
        summary["closing_opening"] = {
            "status": "missing_closing",
            "message": "Closing cash not submitted",
            "difference": None,
            "opening_total": money_value(today_opening.total_cash),
            "closing_total": None,
            "opening_date": today_opening.log_date.strftime("%Y-%m-%d"),
            "closing_date": None,
        }
    elif not today_opening and previous_closing:
        summary["closing_opening"] = {
            "status": "missing_opening",
            "message": "Opening cash not submitted",
            "difference": None,
            "opening_total": None,
            "closing_total": money_value(previous_closing.total_cash),
            "opening_date": None,
            "closing_date": previous_closing.log_date.strftime("%Y-%m-%d"),
        }

    if today_midshift:
        difference = money_value(today_midshift.cash_over_short)

        summary["dayshift"] = {
            "status": "balanced" if difference == 0 else "variance",
            "message": "Dayshift cash submitted",
            "difference": difference,
            "total_cash": money_value(today_midshift.total_cash),
            "amount_to_account_for": money_value(today_midshift.amount_to_account_for),
            "log_date": today_midshift.log_date.strftime("%Y-%m-%d"),
        }

    return summary


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
    user_store = session.get("user_store")

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
    heatmap_items = []

    today = business_date_et()

    manager_cash_summary = None
    if user_role == "manager" and user_store:
        manager_cash_summary = build_manager_cash_summary(user_store, today)

    daily_rows = DailyChecklist.query.options(
        selectinload(DailyChecklist.items)
    ).filter(
        DailyChecklist.checklist_date == today,
        DailyChecklist.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    daily_map = {}
    for row in sorted(daily_rows, key=lambda item: item.id):
        daily_map[row.store_number] = row

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

        heatmap_status = "progress"
        if opening_percent == 0 and integrity_score == 0:
            heatmap_status = "not_started"
        elif integrity_score > 0 and integrity_score < 80:
            heatmap_status = "risk"
        elif opening_percent >= 100 and integrity_score >= 90:
            heatmap_status = "strong"

        heatmap_items.append({
            "area_name": store.area_name,
            "store": store_payload,
            "status": heatmap_status,
        })

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

    heatmap_items = sorted(
        heatmap_items,
        key=lambda item: int(item["store"]["store_number"]) if str(item["store"]["store_number"]).isdigit() else 99999
    )

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
        SVRReport.visit_date >= week_start,
        SVRReport.store_number.in_(visible_store_numbers)
    ).all() if visible_store_numbers else []

    weekly_svr_store_numbers = {
        report.store_number
        for report in weekly_svr_reports
    }

    svr_completed_count = len(weekly_svr_store_numbers)
    svr_missing_stores = sorted(list(visible_store_numbers - weekly_svr_store_numbers))
    svr_compliance_percent = round((svr_completed_count / total_stores) * 100, 1) if total_stores else 0.0

    if visible_store_numbers:
        open_maintenance_count = MaintenanceTicket.query.filter(
            MaintenanceTicket.store_number.in_(visible_store_numbers),
            MaintenanceTicket.status != "complete"
        ).count()

        complete_maintenance_count = MaintenanceTicket.query.filter(
            MaintenanceTicket.store_number.in_(visible_store_numbers),
            MaintenanceTicket.status == "complete"
        ).count()
    else:
        open_maintenance_count = 0
        complete_maintenance_count = 0

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
        "heatmap_items": heatmap_items,
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
        "manager_cash_summary": manager_cash_summary,
    }



ACCOUNT_ROLE_OPTIONS = [
    ("admin", "Admin"),
    ("supervisor", "Supervisor"),
    ("general_manager", "General Manager"),
    ("manager", "Manager / Shift Runner"),
    ("tm", "TM"),
    ("maintenance", "Maintenance"),
    ("hr", "HR"),
]


DEFAULT_MODULE_ACCESS = [
    {
        "module_key": "dashboard",
        "module_label": "Dashboard",
        "module_group": "Command",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager", "tm", "maintenance", "hr"],
        "sort_order": 10,
    },
    {
        "module_key": "store_dashboard",
        "module_label": "Store Dashboard",
        "module_group": "Command",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager", "tm"],
        "sort_order": 20,
    },
    {
        "module_key": "registration_requests",
        "module_label": "Registration Requests",
        "module_group": "People",
        "allowed_roles": ["admin", "supervisor", "general_manager", "hr"],
        "sort_order": 30,
    },
    {
        "module_key": "registration_qr",
        "module_label": "Registration QR Center",
        "module_group": "People",
        "allowed_roles": ["admin", "supervisor", "general_manager", "hr"],
        "sort_order": 40,
    },
    {
        "module_key": "users",
        "module_label": "User Management",
        "module_group": "People",
        "allowed_roles": ["admin", "general_manager"],
        "sort_order": 50,
    },
    {
        "module_key": "checklist",
        "module_label": "Checklist",
        "module_group": "Daily Ops",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager"],
        "sort_order": 60,
    },
    {
        "module_key": "forms",
        "module_label": "Forms",
        "module_group": "Daily Ops",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager"],
        "sort_order": 70,
    },
    {
        "module_key": "prep",
        "module_label": "Prep",
        "module_group": "Daily Ops",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager"],
        "sort_order": 80,
    },
    {
        "module_key": "cash_review",
        "module_label": "Cash Review",
        "module_group": "Daily Ops",
        "allowed_roles": ["admin", "supervisor"],
        "sort_order": 90,
    },
    {
        "module_key": "shift_todos",
        "module_label": "Shift To-Dos",
        "module_group": "Daily Ops",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager", "tm"],
        "sort_order": 100,
    },
    {
        "module_key": "reports",
        "module_label": "Reports",
        "module_group": "Review",
        "allowed_roles": ["admin", "supervisor"],
        "sort_order": 110,
    },
    {
        "module_key": "svr",
        "module_label": "SVR",
        "module_group": "Review",
        "allowed_roles": ["admin", "supervisor"],
        "sort_order": 120,
    },
    {
        "module_key": "verification",
        "module_label": "Verification",
        "module_group": "Review",
        "allowed_roles": ["admin", "supervisor"],
        "sort_order": 130,
    },
    {
        "module_key": "maintenance",
        "module_label": "Maintenance",
        "module_group": "Review",
        "allowed_roles": ["admin", "supervisor", "general_manager", "maintenance"],
        "sort_order": 140,
    },
    {
        "module_key": "maintenance_time_cards",
        "module_label": "Maintenance Time Cards",
        "module_group": "Review",
        "allowed_roles": ["admin", "supervisor", "maintenance"],
        "sort_order": 150,
    },
    {
        "module_key": "nightly_numbers",
        "module_label": "Nightly Numbers",
        "module_group": "Closeout",
        "allowed_roles": ["admin", "supervisor", "general_manager", "manager"],
        "sort_order": 160,
    },
    {
        "module_key": "admin_center",
        "module_label": "Admin Center",
        "module_group": "Admin",
        "allowed_roles": ["admin", "supervisor"],
        "sort_order": 170,
    },
    {
        "module_key": "module_access",
        "module_label": "Module Access",
        "module_group": "Admin",
        "allowed_roles": ["admin"],
        "sort_order": 180,
    },
    {
        "module_key": "store_admin",
        "module_label": "Store Admin",
        "module_group": "Admin",
        "allowed_roles": ["admin"],
        "sort_order": 190,
    },
]


def seed_module_access_settings():
    changed = False

    for item in DEFAULT_MODULE_ACCESS:
        setting = ModuleAccessSetting.query.filter_by(module_key=item["module_key"]).first()

        if not setting:
            setting = ModuleAccessSetting(
                module_key=item["module_key"],
                module_label=item["module_label"],
                module_group=item["module_group"],
                allowed_roles_json=json.dumps(item["allowed_roles"]),
                is_enabled=True,
                sort_order=item["sort_order"],
            )
            db.session.add(setting)
            changed = True
        else:
            # Keep labels/groups/order fresh, but do not overwrite custom role choices.
            setting.module_label = item["module_label"]
            setting.module_group = item["module_group"]
            setting.sort_order = item["sort_order"]

    if changed:
        db.session.commit()


def module_access_allowed_roles(setting):
    try:
        roles = json.loads(setting.allowed_roles_json or "[]")
    except Exception:
        roles = []

    return roles if isinstance(roles, list) else []


def grouped_module_access_settings():
    seed_module_access_settings()

    settings = ModuleAccessSetting.query.order_by(
        ModuleAccessSetting.module_group.asc(),
        ModuleAccessSetting.sort_order.asc(),
        ModuleAccessSetting.module_label.asc(),
    ).all()

    grouped = defaultdict(list)

    for setting in settings:
        grouped[setting.module_group].append(setting)

    preferred_order = ["Command", "People", "Daily Ops", "Review", "Closeout", "Admin"]
    ordered_groups = []

    for group in preferred_order:
        if group in grouped:
            ordered_groups.append((group, grouped.pop(group)))

    for group in sorted(grouped.keys()):
        ordered_groups.append((group, grouped[group]))

    return ordered_groups



@dashboard_bp.route("/")
@login_required
def home():
    user_role = session.get("user_role", "manager")

    data = build_dashboard_data()

    if user_role == "hr" or session.get("account_role") == "hr":
        quick_actions = [
            {"label": "HR Documents", "url": "/hr-documents/"},
            {"label": "Upload Document", "url": "/hr-documents/new"},
            {"label": "Forms Admin", "url": "/forms/admin"},
            {"label": "Registration Requests", "url": "/users/registration-requests"},
            {"label": "QR Center", "url": "/users/registration-qr"},
            {"label": "Time Cards", "url": "/maintenance/time-cards"},
        ]
    elif user_role == "tm":
        quick_actions = [
            {"label": "Training & Standards", "url": "#tm-training"},
            {"label": "Image Standards", "url": "#tm-image"},
            {"label": "Announcements", "url": "#tm-announcements"},
            {"label": "Acknowledgements", "url": "#tm-acknowledgements"},
        ]
    elif user_role == "maintenance":
        quick_actions = [
            {"label": "Open Maintenance", "url": "/maintenance/"},
            {"label": "Time Card", "url": "/maintenance/time-card"},
        ]
    elif user_role == "manager":
        quick_actions = [
            {"label": "Open Checklist", "url": "/checklist/"},
            {"label": "Prep", "url": "/prep/"},
            {"label": "Cash Control", "url": "/cash/"},
            {"label": "Nightly Numbers", "url": "/nightly-numbers/"},
        ]
    else:
        quick_actions = [
            {"label": "Open Checklist", "url": "/checklist/"},
            {"label": "Cash Review", "url": "/cash-review/"},
            {"label": "Reports", "url": "/reports/"},
            {"label": "Open Maintenance", "url": "/maintenance/"},
            {"label": "Action Board", "url": "/action-board"},
            {"label": "Open SVR", "url": "/svr/"},
            {"label": "Verification", "url": "/verification/new"},
        ]

        if user_role == "admin":
            quick_actions.append({"label": "Manage Users", "url": "/users"})
            quick_actions.append({"label": "Manage Stores", "url": "/store-admin/"})
            quick_actions.append({"label": "SVR Admin", "url": "/svr/admin"})


    tm_dwp_records = []
    tm_dwp_pending_count = 0
    tm_dwp_total_count = 0

    if user_role == "tm" and session.get("user_id"):
        tm_user_id = session.get("user_id")

        tm_dwp_records = (
            DWPRecord.query
            .filter(DWPRecord.team_member_id == tm_user_id)
            .order_by(
                DWPRecord.acknowledged_at.isnot(None),
                DWPRecord.conversation_date.desc(),
                DWPRecord.created_at.desc(),
            )
            .limit(5)
            .all()
        )

        tm_dwp_total_count = (
            DWPRecord.query
            .filter(DWPRecord.team_member_id == tm_user_id)
            .count()
        )

        tm_dwp_pending_count = (
            DWPRecord.query
            .filter(
                DWPRecord.team_member_id == tm_user_id,
                DWPRecord.acknowledged_at.is_(None),
            )
            .count()
        )

    return render_template(
        "dashboard.html",
        stats=data["stats"],
        alerts=data["alerts"],
        quick_actions=quick_actions,
        user_name=session.get("user_name"),
        user_role=user_role,
        account_role=session.get("account_role", user_role),
        role_label=session.get("role_label", user_role.title()),
        area_groups=data["area_groups"],
        heatmap_items=data["heatmap_items"],
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
        manager_cash_summary=data["manager_cash_summary"],
        tm_dwp_records=tm_dwp_records,
        tm_dwp_pending_count=tm_dwp_pending_count,
        tm_dwp_total_count=tm_dwp_total_count,
    )



@dashboard_bp.route("/admin-center")
def admin_center():
    raw_roles = [
        session.get("access_role"),
        session.get("role"),
        session.get("account_role"),
        session.get("user_role"),
    ]

    roles = {
        str(role).strip().lower()
        for role in raw_roles
        if role
    }

    is_admin = "admin" in roles
    is_supervisor = "supervisor" in roles
    is_manager = "manager" in roles

    if not (is_admin or is_supervisor or is_manager):
        flash("You do not have access to Admin Center.", "error")
        return redirect(url_for("dashboard.home"))

    tools = []

    account_role = session.get("access_role") or session.get("user_role") or session.get("role")

    if account_role == "manager":
        tools.extend([
            {
                "title": "QR Center",
                "eyebrow": "People",
                "description": "Print registration QR codes for your store.",
                "url": url_for("auth.registration_qr_center"),
                "status": "Manager",
                "icon": "▣",
            },
        ])

    if account_role == "supervisor":
        tools.extend([
            {
                "title": "Forms Admin",
                "eyebrow": "Forms",
                "description": "Manage forms and review submissions for assigned stores.",
                "url": url_for("forms.admin"),
                "status": "Supervisor",
                "icon": "📝",
            },
            {
                "title": "Registration Requests",
                "eyebrow": "People",
                "description": "Approve or reject account requests for assigned stores.",
                "url": url_for("auth.registration_requests"),
                "status": "Supervisor",
                "icon": "＋",
            },
            {
                "title": "QR Center",
                "eyebrow": "People",
                "description": "Print registration QR codes for assigned stores.",
                "url": url_for("auth.registration_qr_center"),
                "status": "Supervisor",
                "icon": "▣",
            },
            {
                "title": "Users & Roles",
                "eyebrow": "People",
                "description": "View users for assigned stores.",
                "url": url_for("auth.manage_users"),
                "status": "View Only",
                "icon": "👥",
            },
            {
                "title": "BPI Connect",
                "eyebrow": "Messaging",
                "description": "Read-only app admin and integration status.",
                "url": url_for("connect_admin.index"),
                "status": "View Only",
                "icon": "💬",
            },
        ])

    if is_admin:
        tools.extend([
            {
                "title": "Checklist Admin",
                "eyebrow": "Daily Ops",
                "description": "Manage checklist items, settings, integrity, and summary controls.",
                "url": url_for("checklist.admin"),
                "status": "Admin",
                "icon": "☑",
            },
            {
                "title": "Forms Admin",
                "eyebrow": "Inspections",
                "description": "Manage morning inspection templates and form setup.",
                "url": url_for("forms.admin"),
                "status": "Admin",
                "icon": "📝",
            },
            {
                "title": "SVR Admin",
                "eyebrow": "Supervisor Visits",
                "description": "Edit SVR templates, sections, and report controls.",
                "url": url_for("svr.admin"),
                "status": "Admin",
                "icon": "📋",
            },
            {
                "title": "Verification Admin",
                "eyebrow": "Compliance",
                "description": "Manage verification questions and weekly submission flow.",
                "url": url_for("verification.admin"),
                "status": "Admin",
                "icon": "🛡",
            },
            {
                "title": "Nightly Numbers Admin",
                "eyebrow": "Closeout",
                "description": "Review and edit nightly numbers submissions.",
                "url": url_for("nightly_numbers.admin"),
                "status": "Admin",
                "icon": "🌙",
            },
            {
                "title": "Users & Roles",
                "eyebrow": "Access",
                "description": "Manage user accounts, roles, and access levels.",
                "url": url_for("auth.manage_users"),
                "status": "Admin",
                "icon": "👥",
            },
            {
                "title": "BPI Connect",
                "eyebrow": "Messaging",
                "description": "Read-only app admin and integration status.",
                "url": url_for("connect_admin.index"),
                "status": "Read Only",
                "icon": "💬",
            },
            {
                "title": "BPI Perks",
                "eyebrow": "Team Offers",
                "description": "Manage partner discounts and offers shown in BPI Connect.",
                "url": url_for("perks.index"),
                "status": "Admin",
                "icon": "🎁",
            },
            {
                "title": "Store Admin",
                "eyebrow": "Company Setup",
                "description": "Manage store setup and company configuration.",
                "url": url_for("store_admin.index"),
                "status": "Admin",
                "icon": "🏪",
            },
            {
                "title": "Module Access",
                "eyebrow": "Permissions",
                "description": "Choose which account roles can see and use each module.",
                "url": url_for("dashboard.module_access_admin"),
                "status": "Admin",
                "icon": "🔐",
            },
        ])

    # Supervisors are allowed here specifically for Prep Admin.
    if is_admin or is_supervisor:
        tools.append({
            "title": "Prep Admin",
            "eyebrow": "Prep System",
            "description": "Manage prep items, par levels, and prep setup.",
            "url": url_for("prep.manage"),
            "status": "Admin" if is_admin else "Supervisor",
            "icon": "🥫",
        })

    return render_template("admin_center.html", tools=tools)


@dashboard_bp.route("/admin-center/module-access", methods=["GET", "POST"])
@login_required
@role_required("admin")
def module_access_admin():
    seed_module_access_settings()

    if request.method == "POST":
        settings = ModuleAccessSetting.query.all()

        for setting in settings:
            setting.is_enabled = request.form.get(f"enabled_{setting.module_key}") == "on"

            selected_roles = []
            for role_key, _role_label in ACCOUNT_ROLE_OPTIONS:
                if request.form.get(f"role_{setting.module_key}_{role_key}") == "on":
                    selected_roles.append(role_key)

            # Guardrail: do not allow Module Access page to become non-admin.
            if setting.module_key == "module_access":
                selected_roles = ["admin"]
                setting.is_enabled = True

            # Guardrail: dashboard must always have admin.
            if setting.module_key == "dashboard" and "admin" not in selected_roles:
                selected_roles.append("admin")

            setting.allowed_roles_json = json.dumps(selected_roles)

        db.session.commit()
        flash("Module access settings updated.", "success")
        return redirect(url_for("dashboard.module_access_admin"))

    return render_template(
        "module_access_admin.html",
        grouped_settings=grouped_module_access_settings(),
        role_options=ACCOUNT_ROLE_OPTIONS,
        module_access_allowed_roles=module_access_allowed_roles,
    )



@dashboard_bp.route("/live-data")
@login_required
def live_data():
    try:
        data = build_dashboard_data()
        return jsonify(data)
    except OperationalError:
        # Render/Postgres can occasionally hand us a stale connection.
        # Clear the bad session and retry once so dashboard auto-refresh does not 500.
        db.session.rollback()
        db.session.remove()

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
