from datetime import date, datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    ChecklistTemplateItem,
    ChecklistOAMapping,
    DailyChecklist,
    DailyChecklistItem,
    Store,
    ChecklistException,
    User,
    IntegritySettings,
    ChecklistAutoEmailSettings,
    ChecklistAutoEmailLog,
)
from app.services.email_service import send_email

from app.services.doughy_execution import build_execution_snapshot

checklist_bp = Blueprint("checklist", __name__, url_prefix="/checklist")

APP_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def now_et():
    return datetime.now(APP_TZ)


def today_et():
    return now_et().date()


def current_ops_date():
    now = now_et()
    if now.hour < 5:
        return now.date() - timedelta(days=1)
    return now.date()


def get_or_create_auto_email_settings():
    settings = ChecklistAutoEmailSettings.query.first()
    if not settings:
        settings = ChecklistAutoEmailSettings()
        db.session.add(settings)
        db.session.commit()
    return settings


def is_past_ops_day(checklist_date: date):
    return checklist_date < current_ops_date()


def utc_naive_to_et(dt):
    if not dt:
        return None
    return dt.replace(tzinfo=UTC_TZ).astimezone(APP_TZ)


def get_or_create_daily_checklist(store_number: str, checklist_date: date):
    daily = (
        DailyChecklist.query
        .filter_by(
            store_number=store_number,
            checklist_date=checklist_date
        )
        .order_by(DailyChecklist.id.desc())
        .first()
    )

    if daily:
        return daily

    daily = DailyChecklist(
        store_number=store_number,
        checklist_date=checklist_date,
        status="in_progress",
        percent_complete=0.0,
        integrity_score=0.0,
        integrity_possible=0,
    )
    db.session.add(daily)
    db.session.flush()

    template_items = ChecklistTemplateItem.query.filter_by(is_active=True).order_by(
        ChecklistTemplateItem.sort_order.asc()
    ).all()

    for template in template_items:
        item = DailyChecklistItem(
            daily_checklist_id=daily.id,
            template_item_id=template.id,
            section_name=template.section_name,
            task_text=template.task_text,
            expected_minutes=template.expected_minutes,
            is_required=template.is_required,
            is_completed=False,
            notes=""
        )
        db.session.add(item)

    db.session.commit()
    return daily


def calculate_manager_walk_integrity(daily: DailyChecklist):
    manager_walk_items = [
        item for item in daily.items
        if item.section_name == "Manager's Walk" and item.is_required
    ]

    if not manager_walk_items:
        return 0.0

    completed_manager_walk = sum(1 for item in manager_walk_items if item.is_completed)
    completion_score = (completed_manager_walk / len(manager_walk_items)) * 100

    expected_minutes = sum(item.expected_minutes or 0 for item in manager_walk_items)

    valid_completed_times = []
    for item in manager_walk_items:
        if not item.completed_at:
            continue

        completed_et = utc_naive_to_et(item.completed_at)
        if completed_et and completed_et.date() == daily.checklist_date:
            valid_completed_times.append(item.completed_at)

    completed_times = sorted(valid_completed_times)

    timing_score = 0.0

    burst_threshold = 3
    burst_window_seconds = 45

    burst_detected = False
    if len(completed_times) >= burst_threshold:
        for i in range(len(completed_times) - burst_threshold + 1):
            start = completed_times[i]
            end = completed_times[i + burst_threshold - 1]
            if (end - start).total_seconds() <= burst_window_seconds:
                burst_detected = True
                break

    if burst_detected:
        timing_score = 0.0
    elif len(completed_times) >= 2 and expected_minutes > 0:
        first_completed = completed_times[0]
        last_completed = completed_times[-1]

        elapsed_minutes = (last_completed - first_completed).total_seconds() / 60
        ratio = elapsed_minutes / expected_minutes

        if ratio >= 0.70:
            timing_score = 100.0
        elif ratio >= 0.50:
            timing_score = 75.0
        elif ratio >= 0.30:
            timing_score = 40.0
        else:
            timing_score = 0.0

    return round((completion_score * 0.60) + (timing_score * 0.40), 1)


def update_checklist_progress(daily: DailyChecklist):
    total_items = len(daily.items)
    completed_items = sum(1 for item in daily.items if item.is_completed)

    if total_items == 0:
        daily.percent_complete = 0.0
    else:
        daily.percent_complete = round((completed_items / total_items) * 100, 1)

    settings = IntegritySettings.query.first()

    integrity_section = (
        settings.integrity_section
        if settings and settings.integrity_section
        else "Before Open / Before 10:30"
    )

    completion_weight = (
        settings.completion_weight
        if settings and settings.completion_weight is not None
        else 0.60
    )

    timing_weight = (
        settings.timing_weight
        if settings and settings.timing_weight is not None
        else 0.40
    )

    burst_threshold = (
        settings.burst_threshold
        if settings and settings.burst_threshold is not None
        else 4
    )

    burst_window_seconds = (
        settings.burst_window_seconds
        if settings and settings.burst_window_seconds is not None
        else 60
    )

    full_score_ratio = (
        settings.full_score_ratio
        if settings and settings.full_score_ratio is not None
        else 0.70
    )

    medium_score_ratio = (
        settings.medium_score_ratio
        if settings and settings.medium_score_ratio is not None
        else 0.50
    )

    low_score_ratio = (
        settings.low_score_ratio
        if settings and settings.low_score_ratio is not None
        else 0.30
    )

    section_one_items = [
        item for item in daily.items
        if item.section_name == integrity_section and item.is_required
    ]

    daily.integrity_possible = len(section_one_items)

    if not section_one_items:
        daily.integrity_score = 0.0
    else:
        completed_section_one = sum(1 for item in section_one_items if item.is_completed)
        completion_score = (completed_section_one / len(section_one_items)) * 100

        expected_minutes = sum(item.expected_minutes or 0 for item in section_one_items)

        valid_completed_times = []
        for item in section_one_items:
            if not item.completed_at:
                continue

            completed_et = utc_naive_to_et(item.completed_at)
            if completed_et and completed_et.date() == daily.checklist_date:
                valid_completed_times.append(item.completed_at)

        completed_times = sorted(valid_completed_times)

        timing_score = 0.0

        burst_detected = False
        if len(completed_times) >= burst_threshold:
            for i in range(len(completed_times) - burst_threshold + 1):
                start = completed_times[i]
                end = completed_times[i + burst_threshold - 1]
                if (end - start).total_seconds() <= burst_window_seconds:
                    burst_detected = True
                    break

        if burst_detected:
            timing_score = 0.0
        elif len(completed_times) >= 2 and expected_minutes > 0:
            first_completed = completed_times[0]
            last_completed = completed_times[-1]

            elapsed_minutes = (last_completed - first_completed).total_seconds() / 60
            ratio = elapsed_minutes / expected_minutes

            if ratio >= full_score_ratio:
                timing_score = 100.0
            elif ratio >= medium_score_ratio:
                timing_score = 75.0
            elif ratio >= low_score_ratio:
                timing_score = 40.0
            else:
                timing_score = 0.0

        daily.integrity_score = round((completion_score * completion_weight) + (timing_score * timing_weight), 1)

    daily.status = "completed" if completed_items == total_items and total_items > 0 else "in_progress"
    db.session.commit()


def build_section_stats(daily: DailyChecklist):
    section_order = [
        "Before Open / Before 10:30",
        "During Dayshift",
        "3-O'Clock Restock",
        "Manager's Walk",
    ]

    stats = {}
    for idx, section_name in enumerate(section_order):
        items = [item for item in daily.items if item.section_name == section_name]
        total = len(items)
        completed = sum(1 for item in items if item.is_completed)
        percent = round((completed / total) * 100) if total else 0

        stats[str(idx)] = {
            "section_name": section_name,
            "completed": completed,
            "total": total,
            "percent": percent,
        }

    return stats


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def run_checklist_closeout(closeout_date: date):
    active_stores = Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    created_count = 0
    skipped_count = 0
    skipped_existing_count = 0
    skipped_complete_count = 0
    not_started_count = 0
    archived_shell_count = 0

    for store in active_stores:
        existing_exception = ChecklistException.query.filter_by(
            store_number=store.store_number,
            checklist_date=closeout_date,
            closeout_type="auto_5am"
        ).first()

        if existing_exception:
            skipped_count += 1
            skipped_existing_count += 1
            continue

        daily = DailyChecklist.query.filter_by(
            store_number=store.store_number,
            checklist_date=closeout_date
        ).first()

        if not daily:
            daily = get_or_create_daily_checklist(store.store_number, closeout_date)
            daily.status = "in_progress"
            daily.percent_complete = 0.0
            daily.integrity_score = 0.0
            daily.manager_on_duty = daily.manager_on_duty or ""
            daily.opening_manager = daily.opening_manager or ""
            daily.closing_manager = daily.closing_manager or ""
            db.session.commit()
            archived_shell_count += 1

        incomplete_items = [item for item in daily.items if not item.is_completed]
        manager_walk_items = [item for item in daily.items if item.section_name == "Manager's Walk"]
        manager_walk_missed = any(not item.is_completed for item in manager_walk_items) if manager_walk_items else False

        checklist_started = (
            bool((daily.opening_manager or "").strip())
            or bool((daily.closing_manager or "").strip())
            or bool((daily.manager_on_duty or "").strip())
            or any(item.is_completed or (item.notes or "").strip() for item in daily.items)
        )

        checklist_completed = len(incomplete_items) == 0 or daily.status == "completed"

        if not checklist_started:
            db.session.add(
                ChecklistException(
                    store_number=store.store_number,
                    checklist_date=closeout_date,
                    manager_on_duty=None,
                    checklist_started=False,
                    checklist_completed=False,
                    manager_walk_missed=True,
                    percent_complete=0.0,
                    integrity_score=0.0,
                    incomplete_task_count=len(daily.items),
                    incomplete_task_names="Checklist not started",
                    auto_closed_at=datetime.utcnow(),
                    closeout_type="auto_5am",
                )
            )
            created_count += 1
            not_started_count += 1
            continue

        if checklist_completed and not manager_walk_missed:
            skipped_count += 1
            skipped_complete_count += 1
            continue

        incomplete_names = ", ".join(item.task_text for item in incomplete_items)

        display_manager = (
            (daily.opening_manager or "").strip()
            or (daily.closing_manager or "").strip()
            or (daily.manager_on_duty or "").strip()
            or None
        )

        db.session.add(
            ChecklistException(
                store_number=store.store_number,
                checklist_date=closeout_date,
                manager_on_duty=display_manager,
                checklist_started=True,
                checklist_completed=checklist_completed,
                manager_walk_missed=manager_walk_missed,
                percent_complete=daily.percent_complete or 0.0,
                integrity_score=daily.integrity_score or 0.0,
                incomplete_task_count=len(incomplete_items),
                incomplete_task_names=incomplete_names,
                auto_closed_at=datetime.utcnow(),
                closeout_type="auto_5am",
            )
        )
        created_count += 1

    db.session.commit()

    return {
        "closeout_date": closeout_date,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "skipped_existing_count": skipped_existing_count,
        "skipped_complete_count": skipped_complete_count,
        "not_started_count": not_started_count,
        "archived_shell_count": archived_shell_count,
    }


def send_store_summary_email(store_number: str, include_supervisor_cc: bool = True):
    ops_date = current_ops_date()

    checklist = DailyChecklist.query.filter_by(
        store_number=store_number,
        checklist_date=ops_date
    ).first()

    if not checklist:
        return {"success": False, "error": f"No checklist found for store {store_number} for today."}

    manager = User.query.filter_by(
        store_number=store_number,
        role="manager",
        is_active=True
    ).first()

    if not manager:
        return {"success": False, "error": f"No active manager user found for store {store_number}."}

    manager_email = manager.get_notification_email()
    if not manager_email:
        return {"success": False, "error": f"Manager email is not configured for store {store_number}."}

    store = Store.query.filter_by(store_number=store_number).first()

    supervisor = None
    if store:
        supervisor = User.query.filter_by(
            area_name=store.area_name,
            role="supervisor",
            is_active=True
        ).first()

    supervisor_email = supervisor.get_notification_email() if supervisor else None
    cc_email = supervisor_email if include_supervisor_cc else None

    incomplete_items = [item.task_text for item in checklist.items if not item.is_completed]
    manager_walk_integrity = calculate_manager_walk_integrity(checklist)

    if incomplete_items:
        missing_tasks_text = "\n".join(f"- {task}" for task in incomplete_items)
    else:
        missing_tasks_text = "- None"

    send_email(
        to_email=manager_email,
        subject=f"[{round(checklist.percent_complete)}%] Store {store_number} Checklist Summary",
        body=(
            f"Store: {store_number}\n"
            f"Date: {ops_date.strftime('%B %d, %Y')}\n\n"
            f"Completion: {round(checklist.percent_complete, 1)}%\n"
            f"Integrity Score: {round(checklist.integrity_score, 1)}\n"
            f"Manager's Walk Integrity: {round(manager_walk_integrity, 1)}\n"
            f"Status: {checklist.status}\n\n"
            f"Opening Manager: {(checklist.opening_manager or '').strip() or 'Not set'}\n"
            f"Closing Manager: {(checklist.closing_manager or '').strip() or 'Not set'}\n\n"
            f"Missing Tasks:\n{missing_tasks_text}\n\n"
            f"- BPI Ops"
        ),
        cc_emails=cc_email
    )

    return {
        "success": True,
        "manager_email": manager_email,
        "supervisor_email": supervisor_email,
        "store_number": store_number,
        "percent_complete": round(checklist.percent_complete, 1),
        "integrity_score": round(checklist.integrity_score, 1),
        "manager_walk_integrity": round(manager_walk_integrity, 1),
        "status": checklist.status,
    }


def send_owner_summary_email(user_id: int, visible_stores, send_results):
    user = User.query.get(user_id)
    if not user:
        return {"success": False, "error": "Sending user not found."}

    owner_email = user.get_notification_email()
    if not owner_email:
        return {"success": False, "error": "Your notification email is not configured."}

    ops_date = current_ops_date()

    total_visible = len(visible_stores)
    sent_count = len([r for r in send_results if r.get("success")])
    failed_results = [r for r in send_results if not r.get("success")]
    failed_count = len(failed_results)

    store_numbers = [store.store_number for store in visible_stores]

    today_checklists = DailyChecklist.query.filter(
        DailyChecklist.store_number.in_(store_numbers),
        DailyChecklist.checklist_date == ops_date
    ).all() if store_numbers else []

    not_started_count = total_visible - len(today_checklists)
    completed_count = sum(1 for c in today_checklists if c.status == "completed")
    in_progress_count = len(today_checklists) - completed_count

    avg_completion = round(
        sum(c.percent_complete or 0 for c in today_checklists) / len(today_checklists),
        1
    ) if today_checklists else 0.0

    avg_integrity = round(
        sum(c.integrity_score or 0 for c in today_checklists) / len(today_checklists),
        1
    ) if today_checklists else 0.0

    lines = []
    for result in send_results:
        if result.get("success"):
            lines.append(
                f"Store {result['store_number']}: "
                f"{result['percent_complete']}% complete | "
                f"Integrity {result['integrity_score']} | "
                f"Walk {result['manager_walk_integrity']} | "
                f"{result['status'].replace('_', ' ').title()}"
            )

    failed_lines = []
    for result in failed_results[:10]:
        failed_lines.append(f"- {result.get('store_number', 'Unknown')}: {result.get('error', 'Unknown error')}")

    body = (
        f"BPI Ops Send-All Summary\n"
        f"Date: {ops_date.strftime('%B %d, %Y')}\n\n"
        f"Visible Stores: {total_visible}\n"
        f"Emails Sent: {sent_count}\n"
        f"Failed Sends: {failed_count}\n\n"
        f"Completed Stores: {completed_count}\n"
        f"In Progress Stores: {in_progress_count}\n"
        f"Not Started Stores: {not_started_count}\n\n"
        f"Average Completion: {avg_completion}%\n"
        f"Average Integrity: {avg_integrity}%\n\n"
        f"Store Summary:\n"
        f"{chr(10).join(lines) if lines else '- No successful sends'}\n\n"
        f"Failed Sends:\n"
        f"{chr(10).join(failed_lines) if failed_lines else '- None'}\n\n"
        f"- BPI Ops"
    )

    send_email(
        to_email=owner_email,
        subject=f"BPI Ops Send-All Recap - {ops_date.strftime('%b %d, %Y')}",
        body=body
    )

    return {"success": True, "owner_email": owner_email}


@checklist_bp.route("/overview")
@login_required
@role_required("admin", "supervisor", "manager")
def overview():
    if session.get("user_role") == "manager":
        return redirect(url_for("checklist.index"))

    visible_stores = get_visible_stores()
    today = current_ops_date()
    visible_store_numbers = [store.store_number for store in visible_stores]

    not_started = []
    in_progress = []
    completed = []
    recent_archives = []

    today_rows = DailyChecklist.query.options(
        selectinload(DailyChecklist.items)
    ).filter(
        DailyChecklist.store_number.in_(visible_store_numbers),
        DailyChecklist.checklist_date == today
    ).all() if visible_store_numbers else []

    today_map = {
        row.store_number: row
        for row in today_rows
    }

    archive_rows = DailyChecklist.query.options(
        selectinload(DailyChecklist.items)
    ).filter(
        DailyChecklist.store_number.in_(visible_store_numbers),
        DailyChecklist.checklist_date < today
    ).order_by(
        DailyChecklist.checklist_date.desc(),
        DailyChecklist.store_number.asc()
    ).limit(150).all() if visible_store_numbers else []

    archive_count_by_store = {}

    store_map = {
        store.store_number: store
        for store in visible_stores
    }

    for row in archive_rows:
        count = archive_count_by_store.get(row.store_number, 0)
        if count >= 5:
            continue

        store = store_map.get(row.store_number)
        if not store:
            continue

        archive_count_by_store[row.store_number] = count + 1

        recent_archives.append({
            "store_number": store.store_number,
            "store_name": store.store_name or f"Store {store.store_number}",
            "area_name": store.area_name,
            "percent_complete": row.percent_complete,
            "integrity_score": row.integrity_score,
            "manager_walk_integrity": calculate_manager_walk_integrity(row),
            "checklist_date": row.checklist_date,
            "status": row.status,
        })

    for store in visible_stores:
        today_checklist = today_map.get(store.store_number)

        if not today_checklist:
            not_started.append({
                "store_number": store.store_number,
                "store_name": store.store_name or f"Store {store.store_number}",
                "area_name": store.area_name,
            })
        elif today_checklist.status == "completed":
            completed.append({
                "store_number": store.store_number,
                "store_name": store.store_name or f"Store {store.store_number}",
                "area_name": store.area_name,
                "percent_complete": today_checklist.percent_complete,
                "integrity_score": today_checklist.integrity_score,
                "manager_walk_integrity": calculate_manager_walk_integrity(today_checklist),
                "checklist_date": today_checklist.checklist_date,
            })
        else:
            in_progress.append({
                "store_number": store.store_number,
                "store_name": store.store_name or f"Store {store.store_number}",
                "area_name": store.area_name,
                "percent_complete": today_checklist.percent_complete,
                "integrity_score": today_checklist.integrity_score,
                "manager_walk_integrity": calculate_manager_walk_integrity(today_checklist),
                "checklist_date": today_checklist.checklist_date,
            })

    recent_archives = sorted(
        recent_archives,
        key=lambda x: (x["checklist_date"], x["store_number"]),
        reverse=True
    )[:25]

    return render_template(
        "checklist_overview.html",
        not_started=not_started,
        in_progress=in_progress,
        completed=completed,
        recent_archives=recent_archives,
        today_label=today.strftime("%B %d, %Y"),
    )


@checklist_bp.route("/send-summary/<store_number>", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def send_daily_summary(store_number):
    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    if store_number not in visible_store_numbers:
        flash("You do not have access to that store.", "error")
        return redirect(url_for("checklist.overview"))

    try:
        result = send_store_summary_email(store_number)

        if not result["success"]:
            flash(result["error"], "error")
            return redirect(url_for("checklist.overview"))

        if result["supervisor_email"]:
            flash(
                f"Summary sent to {result['manager_email']} and cc'd to {result['supervisor_email']}.",
                "success"
            )
        else:
            flash(
                f"Summary sent to {result['manager_email']}. No supervisor email was configured.",
                "success"
            )

    except Exception as e:
        flash(f"Failed to send summary: {str(e)}", "error")

    return redirect(url_for("checklist.overview"))


def run_checklist_summary_batch(
    visible_stores,
    user_id=None,
    include_store_emails=True,
    include_supervisor_cc=True,
    include_owner_recap=True,
):
    sent_count = 0
    failed_count = 0
    send_results = []

    if include_store_emails:
        for store in visible_stores:
            try:
                result = send_store_summary_email(
                    store.store_number,
                    include_supervisor_cc=include_supervisor_cc
                )
                result["store_number"] = store.store_number
                send_results.append(result)

                if result.get("success"):
                    sent_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                send_results.append({
                    "success": False,
                    "store_number": store.store_number,
                    "error": str(e),
                })

    summary_email_result = None
    if include_owner_recap and user_id:
        try:
            summary_email_result = send_owner_summary_email(
                user_id=user_id,
                visible_stores=visible_stores,
                send_results=send_results
            )
        except Exception as e:
            summary_email_result = {"success": False, "error": str(e)}

    return {
        "sent_count": sent_count,
        "failed_count": failed_count,
        "send_results": send_results,
        "summary_email_result": summary_email_result,
    }


def _section_percent_from_checklist(checklist, section_name):
    if not checklist:
        return None

    items = [
        item for item in checklist.items
        if item.section_name == section_name and item.is_required
    ]

    if not items:
        return None

    completed = sum(1 for item in items if item.is_completed)
    return round((completed / len(items)) * 100, 1)


def _format_percent(value):
    if value is None:
        return "Not Started"

    if float(value).is_integer():
        return f"{int(value)}%"

    return f"{value}%"


def _format_score(value):
    if value is None:
        return "0"

    value = round(value, 1)
    if float(value).is_integer():
        return str(int(value))

    return str(value)


def build_auto_summary_body(title, visible_stores, send_results):
    ops_date = current_ops_date()
    total_visible = len(visible_stores)

    store_numbers = [store.store_number for store in visible_stores]

    today_checklists = DailyChecklist.query.options(
        selectinload(DailyChecklist.items)
    ).filter(
        DailyChecklist.store_number.in_(store_numbers),
        DailyChecklist.checklist_date == ops_date
    ).all() if store_numbers else []

    checklist_by_store = {
        checklist.store_number: checklist
        for checklist in today_checklists
    }

    opening_section = "Before Open / Before 10:30"
    restock_section = "3-O'Clock Restock"

    store_rows = []
    needs_attention = []

    opening_values = []
    integrity_values = []
    restock_values = []

    for store in visible_stores:
        checklist = checklist_by_store.get(store.store_number)

        if checklist:
            opening_percent = _section_percent_from_checklist(checklist, opening_section)
            integrity_score = round(checklist.integrity_score or 0, 1)
            restock_percent = _section_percent_from_checklist(checklist, restock_section)

            if opening_percent is not None:
                opening_values.append(opening_percent)

            integrity_values.append(integrity_score)

            if restock_percent is not None:
                restock_values.append(restock_percent)

            store_rows.append(
                f"{store.store_number}: "
                f"Opening {_format_percent(opening_percent)} | "
                f"Integrity {_format_score(integrity_score)} | "
                f"3PM Restock {_format_percent(restock_percent)}"
            )

            attention_flags = []
            if opening_percent is None or opening_percent < 100:
                attention_flags.append(f"Opening {_format_percent(opening_percent)}")
            if integrity_score < 70:
                attention_flags.append(f"Integrity {_format_score(integrity_score)}")
            if restock_percent is None or restock_percent < 100:
                attention_flags.append(f"3PM Restock {_format_percent(restock_percent)}")

            if attention_flags:
                needs_attention.append(
                    f"- {store.store_number}: " + " | ".join(attention_flags)
                )
        else:
            store_rows.append(
                f"{store.store_number}: "
                f"Opening Not Started | "
                f"Integrity 0 | "
                f"3PM Restock Not Started"
            )
            needs_attention.append(
                f"- {store.store_number}: No checklist started"
            )

    avg_opening = round(sum(opening_values) / len(opening_values), 1) if opening_values else 0.0
    avg_integrity = round(sum(integrity_values) / len(integrity_values), 1) if integrity_values else 0.0
    avg_restock = round(sum(restock_values) / len(restock_values), 1) if restock_values else 0.0

    not_started_count = total_visible - len(today_checklists)

    return (
        f"{title}\n"
        f"Date: {ops_date.strftime('%B %d, %Y')}\n\n"
        f"Company Snapshot:\n"
        f"Visible Stores: {total_visible}\n"
        f"Not Started: {not_started_count}\n"
        f"Opening Average: {_format_percent(avg_opening)}\n"
        f"Integrity Average: {_format_score(avg_integrity)}\n"
        f"3PM Restock Average: {_format_percent(avg_restock)}\n\n"
        f"Needs Attention:\n"
        f"{chr(10).join(needs_attention[:15]) if needs_attention else '- None'}\n\n"
        f"Store Breakdown:\n"
        f"{chr(10).join(store_rows) if store_rows else '- No stores found'}\n\n"
        f"- BPI Ops"
    )

def send_auto_admin_summary_emails(visible_stores, send_results, slot):
    ops_date = current_ops_date()

    users = User.query.filter(
        User.role.in_(["admin", "platform_admin"]),
        User.is_active == True
    ).all()

    sent_to = []
    seen_emails = set()

    for user in users:
        email = user.get_notification_email()
        if not email or email in seen_emails:
            continue

        seen_emails.add(email)
        body = build_auto_summary_body(
            f"BPI Ops Automatic Checklist Summary - {slot.upper()}",
            visible_stores,
            send_results
        )

        send_email(
            to_email=email,
            subject=f"BPI Ops Auto Checklist Summary {slot.upper()} - {ops_date.strftime('%b %d, %Y')}",
            body=body
        )
        sent_to.append(email)

    return sent_to


def send_auto_supervisor_summary_emails(visible_stores, send_results, slot):
    ops_date = current_ops_date()
    supervisors = User.query.filter_by(role="supervisor", is_active=True).all()

    sent_to = []
    seen_emails = set()

    for supervisor in supervisors:
        email = supervisor.get_notification_email()
        if not email or email in seen_emails:
            continue

        supervisor_stores = [
            store for store in visible_stores
            if store.area_name == supervisor.area_name
        ]

        if not supervisor_stores:
            continue

        supervisor_store_numbers = {store.store_number for store in supervisor_stores}
        supervisor_results = [
            result for result in send_results
            if result.get("store_number") in supervisor_store_numbers
        ]

        seen_emails.add(email)
        body = build_auto_summary_body(
            f"BPI Ops Supervisor Checklist Summary - {slot.upper()} - {supervisor.area_name or 'Area'}",
            supervisor_stores,
            supervisor_results
        )

        send_email(
            to_email=email,
            subject=f"BPI Ops Supervisor Checklist Summary {slot.upper()} - {ops_date.strftime('%b %d, %Y')}",
            body=body
        )
        sent_to.append(email)

    return sent_to


def maybe_send_checklist_auto_summaries():
    try:
        settings = ChecklistAutoEmailSettings.query.first()
        if not settings or not settings.enabled:
            return

        now = now_et()
        ops_date = current_ops_date()

        slot = None

        # Do not backfill earlier missed slots.
        # If the first app activity happens after 4 PM, only the 4 PM summary sends.
        if settings.send_4pm and now.hour >= 16:
            slot = "4pm"
        elif settings.send_11am and now.hour >= 11:
            slot = "11am"

        if not slot:
            return

        for slot in [slot]:
            existing = ChecklistAutoEmailLog.query.filter_by(
                summary_date=ops_date,
                slot=slot
            ).first()

            if existing:
                continue

            log = ChecklistAutoEmailLog(
                summary_date=ops_date,
                slot=slot,
                triggered_by="app_activity"
            )
            db.session.add(log)

            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                continue

            visible_stores = Store.query.filter_by(is_active=True).order_by(
                Store.store_number.asc()
            ).all()

            batch_result = run_checklist_summary_batch(
                visible_stores=visible_stores,
                user_id=None,
                include_store_emails=settings.send_store_emails,
                include_supervisor_cc=False,
                include_owner_recap=False,
            )

            if settings.send_admin_summary:
                send_auto_admin_summary_emails(
                    visible_stores=visible_stores,
                    send_results=batch_result["send_results"],
                    slot=slot
                )

            if settings.send_supervisor_summary:
                send_auto_supervisor_summary_emails(
                    visible_stores=visible_stores,
                    send_results=batch_result["send_results"],
                    slot=slot
                )

            log.sent_count = batch_result["sent_count"]
            log.failed_count = batch_result["failed_count"]
            db.session.commit()

    except Exception:
        db.session.rollback()
        return


@checklist_bp.before_app_request
def checklist_auto_email_before_request():
    if request.method != "GET":
        return

    endpoint = request.endpoint or ""
    if endpoint.startswith("static") or endpoint.startswith("auth"):
        return

    if not session.get("user_id"):
        return

    user_role = session.get("user_role")
    if user_role not in ["admin", "supervisor", "manager", "platform_admin"]:
        return

    maybe_send_checklist_auto_summaries()


@checklist_bp.route("/send-all-summaries", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def send_all_summaries():
    visible_stores = get_visible_stores()

    batch_result = run_checklist_summary_batch(
        visible_stores=visible_stores,
        user_id=session.get("user_id"),
        include_store_emails=True,
        include_supervisor_cc=True,
        include_owner_recap=True,
    )

    sent_count = batch_result["sent_count"]
    failed_count = batch_result["failed_count"]
    send_results = batch_result["send_results"]
    summary_email_result = batch_result["summary_email_result"]

    if failed_count == 0:
        flash(f"Sent all store summaries successfully. Total stores sent: {sent_count}.", "success")
    else:
        flash(f"Send all complete. Sent: {sent_count}. Failed: {failed_count}.", "success")

    if summary_email_result and summary_email_result.get("success"):
        flash(f"Your recap email was sent to {summary_email_result['owner_email']}.", "success")
    else:
        flash(
            f"Store summaries sent, but your recap email was not sent: {summary_email_result.get('error', 'Unknown error') if summary_email_result else 'No recap was generated.'}",
            "error"
        )

    for failure in [r for r in send_results if not r.get("success")][:10]:
        flash(f"{failure.get('store_number', 'Unknown')}: {failure.get('error', 'Unknown error')}", "error")

    return redirect(url_for("checklist.overview"))


@checklist_bp.route("/delete-archive", methods=["POST"])
@login_required
@role_required("admin")
def delete_archive():
    store_number = request.form.get("store_number", "").strip()
    checklist_date_str = request.form.get("checklist_date", "").strip()

    if not store_number or not checklist_date_str:
        flash("Missing checklist delete data.", "error")
        return redirect(url_for("checklist.overview"))

    try:
        checklist_date = datetime.strptime(checklist_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid checklist date.", "error")
        return redirect(url_for("checklist.overview"))

    if checklist_date >= current_ops_date():
        flash("Only archived past checklists can be deleted.", "error")
        return redirect(url_for("checklist.overview"))

    daily = DailyChecklist.query.filter_by(
        store_number=store_number,
        checklist_date=checklist_date
    ).first()

    if not daily:
        flash("Checklist archive not found.", "error")
        return redirect(url_for("checklist.overview"))

    exception_rows = ChecklistException.query.filter_by(
        store_number=store_number,
        checklist_date=checklist_date
    ).all()

    for row in exception_rows:
        db.session.delete(row)

    db.session.delete(daily)
    db.session.commit()

    flash(
        f"Deleted archived checklist for store {store_number} on {checklist_date.strftime('%B %d, %Y')}.",
        "success"
    )
    return redirect(url_for("checklist.overview"))


@checklist_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor", "manager")
def index():
    visible_stores = get_visible_stores()

    if not visible_stores:
        flash("No stores are assigned to this user.", "error")
        return render_template(
            "placeholder.html",
            page_title="Checklist Module",
            page_message="No stores are assigned to this user."
        )

    default_store = visible_stores[0].store_number
    store_number = request.args.get("store", default_store).strip()

    allowed_store_numbers = {store.store_number for store in visible_stores}
    if store_number not in allowed_store_numbers:
        store_number = default_store

    requested_date_str = request.args.get("date", "").strip()
    today = current_ops_date()

    if requested_date_str:
        try:
            selected_date = datetime.strptime(requested_date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    is_read_only = selected_date < today

    daily = get_or_create_daily_checklist(store_number, selected_date)

    if daily.manager_on_duty and not daily.opening_manager:
        daily.opening_manager = daily.manager_on_duty
        db.session.commit()

    manager_walk_integrity = calculate_manager_walk_integrity(daily)

    if request.method == "POST":
        if is_read_only:
            flash("Past checklists are read-only.", "error")
            return redirect(
                url_for(
                    "checklist.index",
                    store=store_number,
                    date=selected_date.strftime("%Y-%m-%d")
                )
            )

        daily.manager_on_duty = request.form.get("opening_manager", "").strip()
        daily.opening_manager = request.form.get("opening_manager", "").strip()
        daily.closing_manager = request.form.get("closing_manager", "").strip()

        for item in daily.items:
            checkbox_name = f"item_{item.id}"
            notes_name = f"notes_{item.id}"

            was_completed = item.is_completed
            item.is_completed = checkbox_name in request.form
            item.notes = request.form.get(notes_name, "").strip()

            if item.is_completed and not was_completed:
                item.completed_at = datetime.utcnow()
            elif not item.is_completed:
                item.completed_at = None

        db.session.commit()
        update_checklist_progress(daily)
        manager_walk_integrity = calculate_manager_walk_integrity(daily)

        flash("Checklist saved successfully.", "success")
        return redirect(
            url_for(
                "checklist.index",
                store=store_number,
                date=selected_date.strftime("%Y-%m-%d")
            )
        )

    grouped_items = defaultdict(list)
    for item in sorted(daily.items, key=lambda x: x.id):
        grouped_items[item.section_name].append(item)

    section_order = [
        "Before Open / Before 10:30",
        "During Dayshift",
        "3-O'Clock Restock",
        "Manager's Walk",
    ]

    ordered_grouped_items = {
        section: grouped_items.get(section, [])
        for section in section_order
    }

    history = DailyChecklist.query.filter_by(
        store_number=store_number
    ).order_by(DailyChecklist.checklist_date.desc()).limit(14).all()

    return render_template(
        "checklist.html",
        daily=daily,
        grouped_items=ordered_grouped_items,
        store_number=store_number,
        today_label=selected_date.strftime("%B %d, %Y"),
        selected_date=selected_date.strftime("%Y-%m-%d"),
        stores=visible_stores,
        history=history,
        is_read_only=is_read_only,
        manager_walk_integrity=manager_walk_integrity,
    )


@checklist_bp.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin():
    settings = IntegritySettings.query.first()
    auto_email_settings = get_or_create_auto_email_settings()

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "update_auto_email":
            auto_email_settings.enabled = request.form.get("enabled") == "on"
            auto_email_settings.send_11am = request.form.get("send_11am") == "on"
            auto_email_settings.send_4pm = request.form.get("send_4pm") == "on"
            auto_email_settings.send_store_emails = request.form.get("send_store_emails") == "on"
            auto_email_settings.send_admin_summary = request.form.get("send_admin_summary") == "on"
            auto_email_settings.send_supervisor_summary = request.form.get("send_supervisor_summary") == "on"

            db.session.commit()
            flash("Automatic checklist email settings updated.", "success")
            return redirect(url_for("checklist.admin"))

        if action == "update_integrity":
            if not settings:
                settings = IntegritySettings()
                db.session.add(settings)

            settings.integrity_section = request.form.get("integrity_section", "").strip() or "Before Open / Before 10:30"

            try:
                completion_weight = float(request.form.get("completion_weight", "0.60").strip())
                timing_weight = float(request.form.get("timing_weight", "0.40").strip())
                burst_threshold = int(request.form.get("burst_threshold", "4").strip())
                burst_window_seconds = int(request.form.get("burst_window_seconds", "60").strip())
                full_score_ratio = float(request.form.get("full_score_ratio", "0.70").strip())
                medium_score_ratio = float(request.form.get("medium_score_ratio", "0.50").strip())
                low_score_ratio = float(request.form.get("low_score_ratio", "0.30").strip())
            except ValueError:
                flash("Integrity settings must be valid numbers.", "error")
                return redirect(url_for("checklist.admin"))

            if completion_weight < 0 or completion_weight > 1 or timing_weight < 0 or timing_weight > 1:
                flash("Weights must be between 0.00 and 1.00.", "error")
                return redirect(url_for("checklist.admin"))

            if burst_threshold < 2:
                flash("Burst threshold must be at least 2.", "error")
                return redirect(url_for("checklist.admin"))

            if burst_window_seconds < 1:
                flash("Burst window seconds must be at least 1.", "error")
                return redirect(url_for("checklist.admin"))

            if not (0 <= low_score_ratio <= 1 and 0 <= medium_score_ratio <= 1 and 0 <= full_score_ratio <= 1):
                flash("Timing score ratios must be between 0.00 and 1.00.", "error")
                return redirect(url_for("checklist.admin"))

            if not (full_score_ratio > medium_score_ratio > low_score_ratio):
                flash("Timing score ratios must be in descending order: full > medium > low.", "error")
                return redirect(url_for("checklist.admin"))

            settings.completion_weight = completion_weight
            settings.timing_weight = timing_weight
            settings.burst_threshold = burst_threshold
            settings.burst_window_seconds = burst_window_seconds
            settings.full_score_ratio = full_score_ratio
            settings.medium_score_ratio = medium_score_ratio
            settings.low_score_ratio = low_score_ratio

            db.session.commit()
            flash("Integrity settings updated.", "success")
            return redirect(url_for("checklist.admin"))

        if action == "create":
            section_name = request.form.get("section_name", "").strip()
            task_text = request.form.get("task_text", "").strip()
            expected_minutes = request.form.get("expected_minutes", "0").strip()
            sort_order = request.form.get("sort_order", "999").strip()
            is_required = request.form.get("is_required") == "on"

            if not section_name or not task_text:
                flash("Section and task text are required.", "error")
                return redirect(url_for("checklist.admin"))

            try:
                expected_minutes = int(expected_minutes)
                sort_order = int(sort_order)
            except ValueError:
                flash("Expected minutes and sort order must be numbers.", "error")
                return redirect(url_for("checklist.admin"))

            db.session.add(
                ChecklistTemplateItem(
                    section_name=section_name,
                    task_text=task_text,
                    expected_minutes=expected_minutes,
                    sort_order=sort_order,
                    is_required=is_required,
                    is_active=True,
                )
            )
            db.session.commit()
            flash("Checklist task created.", "success")
            return redirect(url_for("checklist.admin"))

        if action == "update":
            item_id = request.form.get("item_id", "").strip()
            item = ChecklistTemplateItem.query.get(item_id)

            if not item:
                flash("Task not found.", "error")
                return redirect(url_for("checklist.admin"))

            item.section_name = request.form.get("section_name", "").strip()
            item.task_text = request.form.get("task_text", "").strip()

            try:
                item.expected_minutes = int(request.form.get("expected_minutes", "0").strip())
                item.sort_order = int(request.form.get("sort_order", "999").strip())
            except ValueError:
                flash("Expected minutes and sort order must be numbers.", "error")
                return redirect(url_for("checklist.admin"))

            item.is_required = request.form.get("is_required") == "on"
            item.is_active = request.form.get("is_active") == "on"

            db.session.commit()
            flash("Checklist task updated.", "success")
            return redirect(url_for("checklist.admin"))

    items = ChecklistTemplateItem.query.order_by(
        ChecklistTemplateItem.section_name.asc(),
        ChecklistTemplateItem.sort_order.asc(),
        ChecklistTemplateItem.id.asc()
    ).all()

    section_options = [
        "Before Open / Before 10:30",
        "During Dayshift",
        "3-O'Clock Restock",
        "Manager's Walk",
    ]

    return render_template(
        "checklist_admin.html",
        items=items,
        section_options=section_options,
        integrity_settings=settings,
        auto_email_settings=auto_email_settings,
    )



@checklist_bp.route("/admin/execution-snapshot")
@login_required
@role_required("admin")
def execution_snapshot():
    selected_store = (request.args.get("store") or "").strip()
    selected_date_raw = (request.args.get("date") or "").strip()

    latest_checklists = (
        DailyChecklist.query
        .order_by(DailyChecklist.checklist_date.desc(), DailyChecklist.id.desc())
        .limit(50)
        .all()
    )

    store_options = sorted({
        row.store_number
        for row in latest_checklists
        if row.store_number
    })

    if not selected_store and latest_checklists:
        selected_store = latest_checklists[0].store_number

    if selected_date_raw:
        selected_date = selected_date_raw
    elif latest_checklists:
        selected_date = latest_checklists[0].checklist_date.isoformat()
    else:
        selected_date = date.today().isoformat()

    snapshot = None
    if selected_store:
        snapshot = build_execution_snapshot(selected_store, selected_date)

    return render_template(
        "checklist_execution_snapshot.html",
        snapshot=snapshot,
        store_options=store_options,
        selected_store=selected_store,
        selected_date=selected_date,
        latest_checklists=latest_checklists,
    )

@checklist_bp.route("/admin/oa-mapping", methods=["GET", "POST"])
@login_required
@role_required("admin")
def oa_mapping_admin():
    oa_sections = [
        "Critical Operations Elements",
        "Product",
        "Product Procedures",
        "Cleanliness & Food Safety",
        "Brand Image",
        "Brand Safety",
    ]

    oa_items_by_section = {
        "Critical Operations Elements": [
            "Expired/unlabeled products critical",
            "Dough management procedures neglected",
            "Excessive remakes",
            "Lack of cleaning supplies/water/hand sink",
            "Hazardous temperatures past critical thresholds",
            "Pest control past critical thresholds",
            "Mold on food/food-contact surfaces",
            "Appearance/hygiene critical",
            "Mature content on premises",
            "Weapons/drugs/alcohol on premises",
        ],
        "Product": [
            "Great/remake pizzas",
            "Great/remake side items",
        ],
        "Product Procedures": [
            "Dough in-use properly proofed",
            "Dough systems evident and in-use",
            "Pizza procedures in-use",
            "Side item procedures in-use",
            "Dating procedures upheld",
            "Product handling procedures upheld",
            "Pizza Cheese & Pizza Sauce systems evident and in-use",
            "Store set up and PRP",
            "Required tools for producing Domino's product",
        ],
        "Cleanliness & Food Safety": [
            "Store interior clean and in good repair",
            "All products not expired",
            "Refrigerated products within specified temperature ranges",
            "Cooked product meets end bake temperatures",
            "Pest control standards maintained",
            "Oven operational, clean, and in good repair",
            "Walk-in operational, clean, and in good repair",
            "Makelines operational, clean, and in good repair",
            "Personnel appearance and hygiene standards",
            "Food prep surfaces and storage areas clean/in good repair",
            "Sinks operational, clean, and stocked",
            "Smallwares and bakewares clean/in good repair",
        ],
        "Brand Image": [
            "Uniform worn properly / positive brand image",
            "Customer area clean and in good repair",
            "Store exterior clean and in good repair",
            "Domino's Technology operational and clean",
            "Customer Greeting",
            "Delivery vehicles and experts represent positive brand image",
            "Signage clean, illuminated, not damaged",
            "Sufficient clean/in-good-repair hot bags",
        ],
        "Brand Safety": [
            "Store follows safe cash procedures",
            "No weapons/pocket knives/mace/pepper spray/similar items",
            "Security callbacks completed",
        ],
    }

    if request.method == "POST":
        template_item_ids = request.form.getlist("template_item_id")

        for template_item_id in template_item_ids:
            item = ChecklistTemplateItem.query.get(template_item_id)
            if not item:
                continue

            mapping = ChecklistOAMapping.query.filter_by(
                checklist_template_item_id=item.id
            ).first()

            if not mapping:
                mapping = ChecklistOAMapping(checklist_template_item_id=item.id)
                db.session.add(mapping)

            prefix = f"mapping_{item.id}_"

            mapping.oa_section = request.form.get(prefix + "oa_section", "").strip() or None
            mapping.oa_item_name = request.form.get(prefix + "oa_item_name", "").strip() or None
            mapping.notes = request.form.get(prefix + "notes", "").strip() or None
            mapping.is_critical = request.form.get(prefix + "is_critical") == "on"
            mapping.is_active = request.form.get(prefix + "is_active") == "on"

            try:
                mapping.oa_points = float(request.form.get(prefix + "oa_points", "0").strip() or 0)
            except ValueError:
                mapping.oa_points = 0.0

        db.session.commit()
        flash("OA mapping updated.", "success")
        return redirect(url_for("checklist.oa_mapping_admin"))

    items = (
        ChecklistTemplateItem.query
        .order_by(
            ChecklistTemplateItem.section_name.asc(),
            ChecklistTemplateItem.sort_order.asc(),
            ChecklistTemplateItem.id.asc(),
        )
        .all()
    )

    mappings = {
        mapping.checklist_template_item_id: mapping
        for mapping in ChecklistOAMapping.query.all()
    }

    return render_template(
        "checklist_oa_mapping.html",
        items=items,
        mappings=mappings,
        oa_sections=oa_sections,
        oa_items_by_section=oa_items_by_section,
    )


@checklist_bp.route("/run-closeout", methods=["POST"])
@login_required
@role_required("admin")
def run_closeout():
    yesterday = today_et() - timedelta(days=1)
    result = run_checklist_closeout(yesterday)

    flash(
        f"Checklist closeout ran for {result['closeout_date'].strftime('%B %d, %Y')}. "
        f"Exceptions created: {result['created_count']}. "
        f"Stores skipped: {result['skipped_count']}. "
        f"Not started: {result['not_started_count']}. "
        f"Archive shells created: {result['archived_shell_count']}. "
        f"Skipped existing: {result['skipped_existing_count']}. "
        f"Skipped complete: {result['skipped_complete_count']}.",
        "success"
    )
    return redirect(url_for("dashboard.home"))




def detect_burst_warning(daily: DailyChecklist, changed_item: DailyChecklistItem, is_completed: bool):
    if not is_completed or not changed_item.completed_at:
        return False

    settings = IntegritySettings.query.first()

    burst_threshold = (
        settings.burst_threshold
        if settings and settings.burst_threshold is not None
        else 4
    )

    burst_window_seconds = (
        settings.burst_window_seconds
        if settings and settings.burst_window_seconds is not None
        else 60
    )

    if burst_threshold < 2:
        burst_threshold = 2

    if burst_window_seconds < 1:
        burst_window_seconds = 60

    completed_times = []

    for item in daily.items:
        if not item.is_completed or not item.completed_at:
            continue

        if item.section_name != changed_item.section_name:
            continue

        completed_et = utc_naive_to_et(item.completed_at)
        if completed_et and completed_et.date() == daily.checklist_date:
            completed_times.append(item.completed_at)

    completed_times = sorted(completed_times)

    if len(completed_times) < burst_threshold:
        return False

    changed_time = changed_item.completed_at

    for i in range(len(completed_times) - burst_threshold + 1):
        window = completed_times[i:i + burst_threshold]

        if changed_time not in window:
            continue

        start = window[0]
        end = window[-1]

        if (end - start).total_seconds() <= burst_window_seconds:
            return True

    return False

@checklist_bp.route("/autosave-item", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "manager")
def autosave_item():
    data = request.get_json() or {}

    item_id = data.get("item_id")
    is_completed = bool(data.get("is_completed", False))
    notes = (data.get("notes") or "").strip()

    item = DailyChecklistItem.query.get(item_id)
    if not item:
        return jsonify({"success": False, "error": "Item not found"}), 404

    daily = item.daily_checklist
    if is_past_ops_day(daily.checklist_date):
        return jsonify({"success": False, "error": "Past checklists are read-only"}), 400

    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    if daily.store_number not in visible_store_numbers:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    was_completed = item.is_completed

    item.is_completed = is_completed
    item.notes = notes

    if is_completed and not was_completed:
        item.completed_at = datetime.utcnow()
    elif not is_completed:
        item.completed_at = None

    db.session.commit()

    burst_detected = detect_burst_warning(daily, item, is_completed)

    update_checklist_progress(daily)
    manager_walk_integrity = calculate_manager_walk_integrity(daily)

    return jsonify({
        "success": True,
        "overall_completion": daily.percent_complete,
        "integrity_score": daily.integrity_score,
        "manager_walk_integrity": manager_walk_integrity,
        "status": daily.status,
        "sections": build_section_stats(daily),
        "burst_detected": burst_detected,
        "burst_message": (
            "Burst detected. Checklist items are being completed too quickly. "
            "Please only mark tasks complete after the work is actually finished."
        ) if burst_detected else "",
    })


@checklist_bp.route("/autosave-manager", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "manager")
def autosave_manager():
    data = request.get_json() or {}

    store_number = (data.get("store_number") or "").strip()
    selected_date_str = (data.get("selected_date") or "").strip()
    opening_manager = (data.get("opening_manager") or "").strip()
    closing_manager = (data.get("closing_manager") or "").strip()

    if not store_number or not selected_date_str:
        return jsonify({"success": False, "error": "Missing store/date"}), 400

    try:
        selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date"}), 400

    if is_past_ops_day(selected_date):
        return jsonify({"success": False, "error": "Past checklists are read-only"}), 400

    visible_store_numbers = {store.store_number for store in get_visible_stores()}
    if store_number not in visible_store_numbers:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    daily = get_or_create_daily_checklist(store_number, selected_date)
    daily.manager_on_duty = opening_manager
    daily.opening_manager = opening_manager
    daily.closing_manager = closing_manager
    db.session.commit()

    return jsonify({"success": True})
