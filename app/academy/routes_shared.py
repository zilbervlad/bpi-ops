from collections import defaultdict
from datetime import datetime, date
from io import BytesIO

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from app.academy.auth_compat import login_required, current_user

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from app.extensions import db
from app.models import User, MITProfile, MITLevelTemplate, MITLevelProgress, MITTask, MITPromotion

mit_sts_bp = Blueprint("mit_sts", __name__, url_prefix="/academy/mit-sts")


# --------------------------------------------------
# ROLE HELPERS
# --------------------------------------------------

def is_tm():
    return (
        current_user.is_authenticated
        and current_user.role == "tm"
        and not is_mit()
    )


def is_mit():
    if not current_user.is_authenticated:
        return False

    position = str(getattr(current_user, "position", "") or "").strip().lower()

    if position in {
        "mit",
        "manager in training",
        "manager-in-training",
        "manager trainee",
    }:
        return True

    return MITProfile.query.filter_by(user_id=current_user.id).first() is not None


def is_coach():
    if not current_user.is_authenticated:
        return False

    role = str(getattr(current_user, "role", "") or "").strip().lower()
    position = str(getattr(current_user, "position", "") or "").strip().lower()

    return (
        role in {"admin", "supervisor"}
        or position in {
            "coach",
            "training coach",
            "training director",
            "director of training",
        }
    )


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def user_can_access_mit_profile(profile):
    return is_coach() or (
        current_user.is_authenticated
        and profile
        and profile.user_id == current_user.id
    )


def get_task_progress_row(task):
    if not getattr(task, "related_template_item_id", None):
        return None

    return MITLevelProgress.query.filter_by(
        mit_profile_id=task.mit_profile_id,
        template_item_id=task.related_template_item_id,
    ).first()


def redirect_for_task(task):
    if getattr(task, "related_template_item_id", None):
        template = MITLevelTemplate.query.get(task.related_template_item_id)
        if template:
            return redirect(
                url_for(
                    "mit_sts.view_level",
                    mit_id=task.mit_profile_id,
                    level_number=template.level_number,
                )
            )

    return redirect(url_for("mit_sts.view_tasks", mit_id=task.mit_profile_id))



def get_task_counts(mit_profile_id):
    tasks = MITTask.query.filter_by(mit_profile_id=mit_profile_id).all()
    today = date.today()

    open_count = 0
    overdue_count = 0
    submitted_count = 0

    for t in tasks:
        if t.status not in ["verified", "cancelled"]:
            open_count += 1

        if t.due_date and t.due_date < today and t.status not in ["verified", "cancelled", "submitted"]:
            overdue_count += 1

        if t.status == "submitted":
            submitted_count += 1

    return open_count, overdue_count, submitted_count


def calculate_level_progress(mit_profile_id, level_number):
    templates = MITLevelTemplate.query.filter_by(level_number=level_number).all()
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


def task_display_status(task):
    if task.status in ["verified", "cancelled"]:
        return task.status

    if task.due_date and task.due_date < date.today() and task.status not in ["submitted"]:
        return "overdue"

    return task.status


def ensure_progress_rows_for_mit(mit_profile):
    templates = MITLevelTemplate.query.all()
    existing_template_ids = {
        row.template_item_id
        for row in MITLevelProgress.query.filter_by(mit_profile_id=mit_profile.id).all()
    }

    created = False
    for template in templates:
        if template.id not in existing_template_ids:
            db.session.add(
                MITLevelProgress(
                    mit_profile_id=mit_profile.id,
                    template_item_id=template.id,
                    status="not_started",
                )
            )
            created = True

    if created:
        db.session.commit()


def get_target_level(current_level):
    if current_level == 1:
        return "2"
    if current_level == 2:
        return "3"
    return "gm"


def available_user_roles():
    return ["manager", "general_manager", "supervisor", "admin"]


def should_force_mit_role(user):
    # BPI Academy never changes a user's primary BPI Ops role.
    return False


def get_active_task_map(profile_id, level_number=None):
    query = MITTask.query.filter(
        MITTask.mit_profile_id == profile_id,
        MITTask.related_template_item_id.isnot(None),
        MITTask.status.in_(["open", "in_progress", "submitted"]),
    )

    if level_number is not None:
        templates = MITLevelTemplate.query.filter_by(level_number=level_number).all()
        template_ids = [item.id for item in templates]
        if not template_ids:
            return {}
        query = query.filter(MITTask.related_template_item_id.in_(template_ids))

    tasks = query.order_by(MITTask.id.desc()).all()

    task_map = {}
    for task in tasks:
        if task.related_template_item_id not in task_map:
            task_map[task.related_template_item_id] = task

    return task_map


def get_all_linked_task_map(profile_id):
    tasks = MITTask.query.filter(
        MITTask.mit_profile_id == profile_id,
        MITTask.related_template_item_id.isnot(None),
    ).order_by(MITTask.id.desc()).all()

    task_map = {}
    for task in tasks:
        if task.related_template_item_id not in task_map:
            task_map[task.related_template_item_id] = task

    return task_map


def sync_progress_from_task(task, progress):
    if not progress:
        return

    if task.status == "verified":
        progress.status = "complete"
        progress.completed_date = datetime.utcnow().date()
        progress.verified_by_user_id = current_user.id
        task.completed_at = datetime.utcnow()
        return

    task.completed_at = None

    if task.status in ["open", "in_progress", "submitted"]:
        progress.status = "in_progress"
        progress.completed_date = None
        progress.verified_by_user_id = None
        return

    if task.status == "cancelled":
        if progress.status != "complete":
            progress.status = "not_started"
            progress.completed_date = None
            progress.verified_by_user_id = None
