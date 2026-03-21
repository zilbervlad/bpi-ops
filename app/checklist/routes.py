from datetime import date, datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    ChecklistTemplateItem,
    DailyChecklist,
    DailyChecklistItem,
    Store,
    ChecklistException,
    User,
)
from app.services.email_service import send_email

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


def is_past_ops_day(checklist_date: date):
    return checklist_date < current_ops_date()


def utc_naive_to_et(dt):
    if not dt:
        return None
    return dt.replace(tzinfo=UTC_TZ).astimezone(APP_TZ)


def get_or_create_daily_checklist(store_number: str, checklist_date: date):
    daily = DailyChecklist.query.filter_by(
        store_number=store_number,
        checklist_date=checklist_date
    ).first()

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

    section_one_items = [
        item for item in daily.items
        if item.section_name == "Before Open / Before 10:30" and item.is_required
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

        burst_threshold = 4
        burst_window_seconds = 60

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

        daily.integrity_score = round((completion_score * 0.60) + (timing_score * 0.40), 1)

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


def send_store_summary_email(store_number: str):
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
        cc_emails=supervisor_email
    )

    return {
        "success": True,
        "manager_email": manager_email,
        "supervisor_email": supervisor_email,
    }


@checklist_bp.route("/overview")
@login_required
@role_required("admin", "supervisor", "manager")
def overview():
    if session.get("user_role") == "manager":
        return redirect(url_for("checklist.index"))

    visible_stores = get_visible_stores()
    today = current_ops_date()

    not_started = []
    in_progress = []
    completed = []
    recent_archives = []

    for store in visible_stores:
        today_checklist = DailyChecklist.query.filter_by(
            store_number=store.store_number,
            checklist_date=today
        ).first()

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

        archive_rows = DailyChecklist.query.filter(
            DailyChecklist.store_number == store.store_number,
            DailyChecklist.checklist_date < today
        ).order_by(DailyChecklist.checklist_date.desc()).limit(5).all()

        for row in archive_rows:
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


@checklist_bp.route("/send-all-summaries", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def send_all_summaries():
    visible_stores = get_visible_stores()

    sent_count = 0
    failed_count = 0
    failed_stores = []

    for store in visible_stores:
        try:
            result = send_store_summary_email(store.store_number)
            if result["success"]:
                sent_count += 1
            else:
                failed_count += 1
                failed_stores.append(f"{store.store_number}: {result['error']}")
        except Exception as e:
            failed_count += 1
            failed_stores.append(f"{store.store_number}: {str(e)}")

    if failed_count == 0:
        flash(f"Sent all summaries successfully. Total stores sent: {sent_count}.", "success")
    else:
        flash(
            f"Send all complete. Sent: {sent_count}. Failed: {failed_count}.",
            "success"
        )
        for failure in failed_stores[:10]:
            flash(failure, "error")

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
    if request.method == "POST":
        action = request.form.get("action", "").strip()

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

    item.is_completed = is_completed
    item.notes = notes
    item.completed_at = datetime.utcnow() if is_completed else None

    db.session.commit()
    update_checklist_progress(daily)
    manager_walk_integrity = calculate_manager_walk_integrity(daily)

    return jsonify({
        "success": True,
        "overall_completion": daily.percent_complete,
        "integrity_score": daily.integrity_score,
        "manager_walk_integrity": manager_walk_integrity,
        "status": daily.status,
        "sections": build_section_stats(daily),
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