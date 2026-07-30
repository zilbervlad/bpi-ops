from collections import defaultdict
from datetime import date
from sqlalchemy import func, case, and_

from app.extensions import db
from app.models import MITLevelTemplate, MITLevelProgress, MITTask, MITPromotion


def task_display_status(task):
    if task.status in ["verified", "cancelled"]:
        return task.status

    if task.due_date and task.due_date < date.today() and task.status not in ["submitted"]:
        return "overdue"

    return task.status


def calculate_level_progress(mit_profile_id, level_number):
    templates = MITLevelTemplate.query.filter_by(
        level_number=level_number,
        is_required=True
    ).all()

    total = len(templates)
    if total == 0:
        return 0

    template_ids = [item.id for item in templates]

    completed = MITLevelProgress.query.filter(
        MITLevelProgress.mit_profile_id == mit_profile_id,
        MITLevelProgress.template_item_id.in_(template_ids),
        MITLevelProgress.status == "complete",
    ).count()

    return round((completed / total) * 100)


def calculate_overall_progress(mit_profile_id):
    templates = MITLevelTemplate.query.filter_by(is_required=True).all()

    total = len(templates)
    if total == 0:
        return 0

    template_ids = [item.id for item in templates]

    completed = MITLevelProgress.query.filter(
        MITLevelProgress.mit_profile_id == mit_profile_id,
        MITLevelProgress.template_item_id.in_(template_ids),
        MITLevelProgress.status == "complete",
    ).count()

    return round((completed / total) * 100)


def get_next_target_level(current_level):
    if current_level == 1:
        return "2"
    if current_level == 2:
        return "3"
    return "gm"


def is_mit_ready_for_promotion(mit):
    current_level_progress = calculate_level_progress(mit.id, mit.current_level)
    task_counts = get_mit_task_counts(mit.id)

    if mit.sts_status == "blocked":
        return False

    if current_level_progress != 100:
        return False

    if task_counts["open"] > 0 or task_counts["overdue"] > 0 or task_counts["submitted"] > 0:
        return False

    return True


def get_mit_task_counts(mit_profile_id):
    tasks = MITTask.query.filter_by(mit_profile_id=mit_profile_id).all()

    open_count = 0
    overdue_count = 0
    submitted_count = 0

    for task in tasks:
        display_status = task_display_status(task)

        if display_status not in ["verified", "cancelled"]:
            open_count += 1

        if display_status == "overdue":
            overdue_count += 1

        if task.status == "submitted":
            submitted_count += 1

    return {
        "open": open_count,
        "overdue": overdue_count,
        "submitted": submitted_count,
    }


def get_task_counts_map_for_mits(mit_ids):
    if not mit_ids:
        return {}

    today = date.today()

    rows = db.session.query(
        MITTask.mit_profile_id,
        func.sum(case((MITTask.status.notin_(["verified", "cancelled"]), 1), else_=0)).label("open_count"),
        func.sum(case((and_(MITTask.status.notin_(["verified", "cancelled", "submitted"]), MITTask.due_date.isnot(None), MITTask.due_date < today), 1), else_=0)).label("overdue_count"),
        func.sum(case((MITTask.status == "submitted", 1), else_=0)).label("submitted_count"),
    ).filter(MITTask.mit_profile_id.in_(mit_ids)).group_by(MITTask.mit_profile_id).all()

    counts_map = {
        mit_id: {"open": int(open_count or 0), "overdue": int(overdue_count or 0), "submitted": int(submitted_count or 0)}
        for mit_id, open_count, overdue_count, submitted_count in rows
    }

    for mit_id in mit_ids:
        counts_map.setdefault(mit_id, {"open": 0, "overdue": 0, "submitted": 0})

    return counts_map


def refresh_mit_status(mit):
    if mit.sts_status == "blocked":
        return

    if is_mit_ready_for_promotion(mit):
        mit.sts_status = "ready"
    else:
        if mit.sts_status in ["ready", "promoted"]:
            mit.sts_status = "on_track"


def ensure_progress_rows_for_mit(mit_profile):
    templates = MITLevelTemplate.query.all()
    existing_template_ids = {row.template_item_id for row in MITLevelProgress.query.filter_by(mit_profile_id=mit_profile.id).all()}

    created = False
    for template in templates:
        if template.id not in existing_template_ids:
            db.session.add(MITLevelProgress(
                mit_profile_id=mit_profile.id,
                template_item_id=template.id,
                status="not_started",
            ))
            created = True

    if created:
        db.session.commit()
