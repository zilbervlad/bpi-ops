from collections import defaultdict
from datetime import date, timedelta, datetime

from flask import Blueprint, render_template, request, session
from app.auth.routes import login_required, role_required
from app.models import Store, DailyChecklist

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


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


@reports_bp.route("/")
@login_required
@role_required("admin", "supervisor")
def index():
    visible_stores = get_visible_stores()

    today = date.today()
    default_start = today - timedelta(days=6)
    default_end = today

    q = request.args.get("q", "").strip()
    start_date_str = request.args.get("start_date", default_start.strftime("%Y-%m-%d")).strip()
    end_date_str = request.args.get("end_date", default_end.strftime("%Y-%m-%d")).strip()
    selected_store = request.args.get("store_number", "").strip()
    selected_area = request.args.get("area_name", "").strip()
    manager_name = request.args.get("manager_name", "").strip()
    show_task_analysis = request.args.get("show_task_analysis", "").strip() == "1"

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        start_date = default_start
        start_date_str = start_date.strftime("%Y-%m-%d")

    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        end_date = default_end
        end_date_str = end_date.strftime("%Y-%m-%d")

    if end_date < start_date:
        start_date, end_date = end_date, start_date
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

    area_options = sorted({store.area_name for store in visible_stores if store.area_name})

    filtered_stores = visible_stores

    if selected_store:
        filtered_stores = [
            store for store in filtered_stores
            if store.store_number == selected_store
        ]

    if selected_area:
        filtered_stores = [
            store for store in filtered_stores
            if store.area_name == selected_area
        ]

    if q:
        q_lower = q.lower()
        filtered_stores = [
            store for store in filtered_stores
            if (
                q_lower in (store.store_number or "").lower()
                or q_lower in (store.store_name or "").lower()
                or q_lower in (store.area_name or "").lower()
            )
        ]

    filtered_store_numbers = {store.store_number for store in filtered_stores}

    checklist_rows = []
    if filtered_store_numbers:
        checklist_rows = DailyChecklist.query.filter(
            DailyChecklist.store_number.in_(filtered_store_numbers),
            DailyChecklist.checklist_date >= start_date,
            DailyChecklist.checklist_date <= end_date,
        ).order_by(
            DailyChecklist.checklist_date.desc(),
            DailyChecklist.store_number.asc()
        ).all()

    if manager_name:
        manager_lower = manager_name.lower()
        checklist_rows = [
            row for row in checklist_rows
            if (
                manager_lower in (row.manager_on_duty or "").lower()
                or manager_lower in (row.opening_manager or "").lower()
                or manager_lower in (row.closing_manager or "").lower()
            )
        ]

    if q:
        q_lower = q.lower()
        checklist_rows = [
            row for row in checklist_rows
            if (
                q_lower in (row.store_number or "").lower()
                or q_lower in (row.manager_on_duty or "").lower()
                or q_lower in (row.opening_manager or "").lower()
                or q_lower in (row.closing_manager or "").lower()
            )
        ]

    checklist_store_numbers = {row.store_number for row in checklist_rows}

    if selected_store or selected_area or q or manager_name:
        filtered_stores = [
            store for store in filtered_stores
            if store.store_number in checklist_store_numbers
            or (
                q
                and (
                    q.lower() in (store.store_number or "").lower()
                    or q.lower() in (store.store_name or "").lower()
                    or q.lower() in (store.area_name or "").lower()
                )
            )
        ]

    days_in_range = (end_date - start_date).days + 1

    summary = {
        "avg_completion": 0.0,
        "avg_integrity": 0.0,
        "completed_count": 0,
        "in_progress_count": 0,
        "not_started_count": 0,
        "days_in_range": days_in_range,
        "stores_in_scope": len(filtered_stores),
        "total_checklists": len(checklist_rows),
    }

    if checklist_rows:
        summary["avg_completion"] = round(
            sum(row.percent_complete for row in checklist_rows) / len(checklist_rows), 1
        )

        integrity_values = [row.integrity_score for row in checklist_rows if row.integrity_score > 0]
        summary["avg_integrity"] = round(
            sum(integrity_values) / len(integrity_values), 1
        ) if integrity_values else 0.0

        summary["completed_count"] = sum(1 for row in checklist_rows if row.status == "completed")
        summary["in_progress_count"] = sum(1 for row in checklist_rows if row.status == "in_progress")

    possible_checklists = len(filtered_stores) * days_in_range
    summary["not_started_count"] = max(possible_checklists - len(checklist_rows), 0)

    store_report_rows = []

    for store in filtered_stores:
        store_rows = [row for row in checklist_rows if row.store_number == store.store_number]

        checklist_count = len(store_rows)
        avg_completion = round(
            sum(row.percent_complete for row in store_rows) / checklist_count, 1
        ) if checklist_count else 0.0

        integrity_values = [row.integrity_score for row in store_rows if row.integrity_score > 0]
        avg_integrity = round(
            sum(integrity_values) / len(integrity_values), 1
        ) if integrity_values else 0.0

        completed_count = sum(1 for row in store_rows if row.status == "completed")
        in_progress_count = sum(1 for row in store_rows if row.status == "in_progress")
        not_started_count = max(days_in_range - checklist_count, 0)

        manager_names = sorted({
            name.strip()
            for row in store_rows
            for name in [row.manager_on_duty, row.opening_manager, row.closing_manager]
            if name and name.strip()
        })

        store_report_rows.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "area_name": store.area_name,
            "avg_completion": avg_completion,
            "avg_integrity": avg_integrity,
            "completed_count": completed_count,
            "in_progress_count": in_progress_count,
            "not_started_count": not_started_count,
            "checklist_count": checklist_count,
            "manager_names": manager_names,
        })

    store_report_rows = sorted(
        store_report_rows,
        key=lambda x: (x["area_name"], x["store_number"])
    )

    area_rollups = defaultdict(lambda: {
        "store_count": 0,
        "avg_completion_total": 0.0,
        "avg_integrity_total": 0.0,
        "completed_count": 0,
        "in_progress_count": 0,
        "not_started_count": 0,
        "checklist_count": 0,
    })

    for row in store_report_rows:
        rollup = area_rollups[row["area_name"]]
        rollup["store_count"] += 1
        rollup["avg_completion_total"] += row["avg_completion"]
        rollup["avg_integrity_total"] += row["avg_integrity"]
        rollup["completed_count"] += row["completed_count"]
        rollup["in_progress_count"] += row["in_progress_count"]
        rollup["not_started_count"] += row["not_started_count"]
        rollup["checklist_count"] += row["checklist_count"]

    area_report_rows = []
    for area_name, rollup in sorted(area_rollups.items()):
        store_count = rollup["store_count"]
        area_report_rows.append({
            "area_name": area_name,
            "store_count": store_count,
            "avg_completion": round(rollup["avg_completion_total"] / store_count, 1) if store_count else 0.0,
            "avg_integrity": round(rollup["avg_integrity_total"] / store_count, 1) if store_count else 0.0,
            "completed_count": rollup["completed_count"],
            "in_progress_count": rollup["in_progress_count"],
            "not_started_count": rollup["not_started_count"],
            "checklist_count": rollup["checklist_count"],
        })

    task_analysis = None
    if show_task_analysis:
        ranked_stores = sorted(
            [
                row for row in store_report_rows
                if row["checklist_count"] > 0
            ],
            key=lambda x: (
                -x["avg_completion"],
                -x["avg_integrity"],
                -x["completed_count"],
                x["store_number"],
            )
        )

        lowest_integrity = sorted(
            [
                row for row in store_report_rows
                if row["checklist_count"] > 0
            ],
            key=lambda x: (
                x["avg_integrity"],
                x["avg_completion"],
                x["store_number"],
            )
        )

        completion_gaps = sorted(
            [
                row for row in store_report_rows
                if row["not_started_count"] > 0 or row["in_progress_count"] > 0
            ],
            key=lambda x: (
                -x["not_started_count"],
                -x["in_progress_count"],
                x["store_number"],
            )
        )

        task_analysis = {
            "top_ranked_stores": ranked_stores[:5],
            "lowest_integrity_stores": lowest_integrity[:5],
            "biggest_completion_gaps": completion_gaps[:5],
        }

    return render_template(
        "reports.html",
        stores=visible_stores,
        area_options=area_options,
        selected_store=selected_store,
        selected_area=selected_area,
        manager_name=manager_name,
        q=q,
        start_date=start_date_str,
        end_date=end_date_str,
        show_task_analysis=show_task_analysis,
        summary=summary,
        store_report_rows=store_report_rows,
        area_report_rows=area_report_rows,
        task_analysis=task_analysis,
    )