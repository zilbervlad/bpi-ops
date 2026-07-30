from .routes_shared import *
from app.models import MITBinderTemplate, MITBinderSubmission


@mit_sts_bp.route("/mits/<int:mit_id>/level/<int:level_number>")
@login_required
def view_level(mit_id, level_number):
    if level_number not in [1, 2, 3]:
        flash("Invalid level.", "danger")
        return redirect(url_for("mit_sts.view_mit", mit_id=mit_id))

    profile = MITProfile.query.get_or_404(mit_id)

    if not user_can_access_mit_profile(profile):
        return redirect(url_for("mit_sts.dashboard"))

    ensure_progress_rows_for_mit(profile)

    templates = MITLevelTemplate.query.filter_by(level_number=level_number).order_by(
        MITLevelTemplate.category.asc(),
        MITLevelTemplate.sort_order.asc(),
        MITLevelTemplate.id.asc(),
    ).all()

    progress_rows = MITLevelProgress.query.filter_by(mit_profile_id=profile.id).all()
    progress_map = {row.template_item_id: row for row in progress_rows}

    grouped_items = defaultdict(list)
    for template in templates:
        grouped_items[template.category or "General"].append(template)

    active_task_map = get_active_task_map(profile.id, level_number=level_number)
    all_linked_task_map = get_all_linked_task_map(profile.id)

    level_progress = calculate_level_progress(profile.id, level_number)

    binder_templates = MITBinderTemplate.query.filter_by(
        level_number=level_number,
        is_active=True
    ).all()

    binder_required = len(binder_templates) > 0

    binder_template_ids = [template.id for template in binder_templates]
    binder_submission_count = 0

    if binder_template_ids:
        binder_submission_count = MITBinderSubmission.query.filter(
            MITBinderSubmission.mit_profile_id == profile.id,
            MITBinderSubmission.template_id.in_(binder_template_ids)
        ).count()

    binder_completed = (not binder_required) or (binder_submission_count > 0)

    is_complete = level_progress == 100 and len(templates) > 0 and binder_completed

    return render_template(
        "academy/mit_sts/level_detail.html",
        mit=profile,
        level_number=level_number,
        grouped_items=dict(grouped_items),
        progress_map=progress_map,
        active_task_map=active_task_map,
        all_linked_task_map=all_linked_task_map,
        level_progress=level_progress,
        is_complete=is_complete,
        binder_required=binder_required,
        binder_completed=binder_completed,
        binder_submission_count=binder_submission_count,
        binder_template_count=len(binder_templates),
        task_display_status=task_display_status,
        user=current_user,
        can_edit=is_coach(),
        can_manage_templates=is_coach(),
    )


@mit_sts_bp.route("/progress/<int:progress_id>/status", methods=["POST"])
@login_required
def update_progress(progress_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    progress = MITLevelProgress.query.get_or_404(progress_id)
    new_status = request.form.get("status", "not_started").strip()
    return_anchor = request.form.get("return_anchor", "").strip()

    if new_status not in ["not_started", "in_progress", "complete"]:
        flash("Invalid status.", "danger")
        template = MITLevelTemplate.query.get(progress.template_item_id)
        destination = url_for(
            "mit_sts.view_level",
            mit_id=progress.mit_profile_id,
            level_number=template.level_number if template else 1,
        )
        if return_anchor:
            destination += f"#{return_anchor}"
        return redirect(destination)

    progress.status = new_status

    notes = request.form.get("notes", "").strip()
    if notes:
        progress.notes = notes

    linked_tasks = MITTask.query.filter_by(
        mit_profile_id=progress.mit_profile_id,
        related_template_item_id=progress.template_item_id,
    ).order_by(MITTask.id.desc()).all()

    if new_status == "complete":
        progress.completed_date = datetime.utcnow().date()
        progress.verified_by_user_id = current_user.id

        for task in linked_tasks:
            if task.status != "cancelled":
                task.status = "verified"
                task.completed_at = datetime.utcnow()
    elif new_status == "in_progress":
        progress.completed_date = None
        progress.verified_by_user_id = None

        for task in linked_tasks:
            if task.status not in ["cancelled", "verified"]:
                task.status = "in_progress"
                task.completed_at = None
    else:
        progress.completed_date = None
        progress.verified_by_user_id = None

        for task in linked_tasks:
            if task.status != "cancelled":
                task.status = "open"
                task.completed_at = None

    db.session.commit()

    template = MITLevelTemplate.query.get(progress.template_item_id)
    flash("STS item updated.", "success")

    destination = url_for(
        "mit_sts.view_level",
        mit_id=progress.mit_profile_id,
        level_number=template.level_number if template else 1,
    )
    if return_anchor:
        destination += f"#{return_anchor}"

    return redirect(destination)