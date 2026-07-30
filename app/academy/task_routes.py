from .routes_shared import *


def redirect_for_task_with_anchor(task, return_anchor=""):
    profile = MITProfile.query.get_or_404(task.mit_profile_id)
    level_number = profile.current_level or 1

    if getattr(task, "related_template_item_id", None):
        template = MITLevelTemplate.query.get(task.related_template_item_id)
        if template:
            level_number = template.level_number

    destination = url_for(
        "mit_sts.view_level",
        mit_id=profile.id,
        level_number=level_number,
    )
    if return_anchor:
        destination += f"#{return_anchor}"
    return redirect(destination)


@mit_sts_bp.route("/mits/<int:mit_id>/tasks/new", methods=["GET", "POST"])
@login_required
def new_task(mit_id):
    profile = MITProfile.query.get_or_404(mit_id)

    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    flash("Assign tasks from the level page now.", "info")
    return redirect(
        url_for(
            "mit_sts.view_level",
            mit_id=profile.id,
            level_number=profile.current_level or 1,
        )
    )


@mit_sts_bp.route("/tasks/board/<int:progress_id>/assign", methods=["POST"])
@login_required
def assign_board_task(progress_id):
    progress = MITLevelProgress.query.get_or_404(progress_id)
    template = MITLevelTemplate.query.get_or_404(progress.template_item_id)
    profile = MITProfile.query.get_or_404(progress.mit_profile_id)

    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    flash("Assign tasks from the level page now.", "info")
    return redirect(
        url_for(
            "mit_sts.view_level",
            mit_id=profile.id,
            level_number=template.level_number,
        )
    )


@mit_sts_bp.route("/tasks/board/<int:task_id>/manage", methods=["POST"])
@login_required
def manage_board_task(task_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    task = MITTask.query.get_or_404(task_id)
    profile = MITProfile.query.get_or_404(task.mit_profile_id)
    return_anchor = request.form.get("return_anchor", "").strip()

    title = request.form.get("title", "").strip()
    due_date_raw = request.form.get("due_date", "").strip()
    priority = request.form.get("priority", "medium").strip()
    notes = request.form.get("notes", "").strip()

    submit_action = request.form.get("submit_action", "").strip()
    selected_status = request.form.get("status", "").strip()

    if title:
        task.title = title

    if priority in ["low", "medium", "high"]:
        task.priority = priority

    if due_date_raw:
        try:
            task.due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    else:
        task.due_date = None

    task.notes = notes or None

    progress = None
    level_number = profile.current_level or 1

    if getattr(task, "related_template_item_id", None):
        progress = MITLevelProgress.query.filter_by(
            mit_profile_id=task.mit_profile_id,
            template_item_id=task.related_template_item_id,
        ).first()

        template = MITLevelTemplate.query.get(task.related_template_item_id)
        if template:
            level_number = template.level_number

    if submit_action == "unassign":
        db.session.delete(task)

        if progress:
            active_remaining = MITTask.query.filter(
                MITTask.mit_profile_id == progress.mit_profile_id,
                MITTask.related_template_item_id == progress.template_item_id,
                MITTask.status.in_(["open", "in_progress", "submitted"])
            ).count()

            if active_remaining <= 1 and progress.status != "complete":
                progress.status = "not_started"
                progress.completed_date = None
                progress.verified_by_user_id = None

        db.session.commit()
        flash("Task unassigned.", "success")

        destination = url_for(
            "mit_sts.view_level",
            mit_id=profile.id,
            level_number=level_number,
        )
        if return_anchor:
            destination += f"#{return_anchor}"
        return redirect(destination)

    if submit_action == "save":
        if selected_status in ["open", "in_progress", "submitted", "verified", "cancelled"]:
            task.status = selected_status

        sync_progress_from_task(task, progress)

        db.session.commit()
        flash("Task updated.", "success")

        destination = url_for(
            "mit_sts.view_level",
            mit_id=profile.id,
            level_number=level_number,
        )
        if return_anchor:
            destination += f"#{return_anchor}"
        return redirect(destination)

    flash("No action selected.", "danger")
    destination = url_for(
        "mit_sts.view_level",
        mit_id=profile.id,
        level_number=level_number,
    )
    if return_anchor:
        destination += f"#{return_anchor}"
    return redirect(destination)


@mit_sts_bp.route("/tasks/<int:task_id>/quick-add", methods=["POST"])
@login_required
def quick_add_task(task_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    progress = MITLevelProgress.query.get_or_404(task_id)
    template = MITLevelTemplate.query.get_or_404(progress.template_item_id)
    return_anchor = request.form.get("return_anchor", "").strip()

    due_date = request.form.get("due_date", "").strip()
    priority = request.form.get("priority", "medium").strip()
    notes = request.form.get("notes", "").strip()
    title = request.form.get("title", "").strip() or template.item_name

    existing_open_task = MITTask.query.filter(
        MITTask.mit_profile_id == progress.mit_profile_id,
        MITTask.related_template_item_id == template.id,
        MITTask.status.in_(["open", "in_progress", "submitted"])
    ).first()

    destination = url_for(
        "mit_sts.view_level",
        mit_id=progress.mit_profile_id,
        level_number=template.level_number,
    )
    if return_anchor:
        destination += f"#{return_anchor}"

    if existing_open_task:
        flash("There is already an open task linked to this STS item.", "danger")
        return redirect(destination)

    due_date_obj = None
    if due_date:
        try:
            due_date_obj = datetime.strptime(due_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    task = MITTask(
        mit_profile_id=progress.mit_profile_id,
        title=title,
        description=template.item_description or None,
        related_template_item_id=template.id,
        assigned_by_user_id=current_user.id,
        due_date=due_date_obj,
        priority=priority if priority in ["low", "medium", "high"] else "medium",
        status="open",
        notes=notes or None,
    )
    db.session.add(task)

    progress.status = "in_progress"
    progress.completed_date = None
    progress.verified_by_user_id = None

    db.session.commit()

    flash("Task assigned to this STS item.", "success")
    return redirect(destination)


@mit_sts_bp.route("/tasks/<int:mit_id>")
@login_required
def view_tasks(mit_id):
    profile = MITProfile.query.get_or_404(mit_id)

    if not is_coach() and profile.user_id != current_user.id:
        return redirect(url_for("mit_sts.dashboard"))

    all_tasks = (
        MITTask.query
        .filter_by(mit_profile_id=mit_id)
        .order_by(MITTask.id.desc())
        .all()
    )

    active_tasks = []
    completed_tasks = []

    for task in all_tasks:
        display_status = task_display_status(task)
        if task.status in ["verified", "cancelled"]:
            completed_tasks.append((task, display_status))
        else:
            active_tasks.append((task, display_status))

    return render_template(
        "academy/mit_sts/mit_tasks.html",
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        mit=profile,
        user=current_user,
        can_edit=is_coach(),
    )


@mit_sts_bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    task = MITTask.query.get_or_404(task_id)
    profile = MITProfile.query.get_or_404(task.mit_profile_id)
    return_anchor = request.form.get("return_anchor", "").strip()

    if not user_can_access_mit_profile(profile):
        return redirect(url_for("mit_sts.dashboard"))

    requested_status = request.form.get("status", "").strip()

    coach_allowed_statuses = ["open", "in_progress", "submitted", "verified", "cancelled"]
    mit_allowed_statuses = ["in_progress", "submitted"]

    if is_coach():
        allowed_statuses = coach_allowed_statuses
    else:
        if getattr(task, "related_template_item_id", None) is None:
            flash("You cannot update that task from your MIT dashboard.", "danger")
            return redirect_for_task_with_anchor(task, return_anchor)
        allowed_statuses = mit_allowed_statuses

    if requested_status not in allowed_statuses:
        flash("Invalid task status.", "danger")
        return redirect_for_task_with_anchor(task, return_anchor)

    progress = get_task_progress_row(task)

    if not is_coach() and requested_status == "submitted":
        if task.status not in ["open", "in_progress"]:
            flash("Only active tasks can be submitted for verification.", "danger")
            return redirect_for_task_with_anchor(task, return_anchor)

    task.status = requested_status
    sync_progress_from_task(task, progress)
    db.session.commit()

    if requested_status == "submitted":
        flash("Task submitted for verification.", "success")
    elif requested_status == "verified":
        flash("Task verified successfully.", "success")
    else:
        flash("Task status updated.", "success")

    return redirect_for_task_with_anchor(task, return_anchor)


@mit_sts_bp.route("/tasks/<int:task_id>/submit", methods=["POST"])
@login_required
def submit_task_for_verification(task_id):
    task = MITTask.query.get_or_404(task_id)
    profile = MITProfile.query.get_or_404(task.mit_profile_id)
    return_anchor = request.form.get("return_anchor", "").strip()

    if not user_can_access_mit_profile(profile):
        return redirect(url_for("mit_sts.dashboard"))

    if is_coach():
        pass
    elif getattr(task, "related_template_item_id", None) is None:
        flash("That task cannot be submitted from the MIT side.", "danger")
        return redirect_for_task_with_anchor(task, return_anchor)

    if task.status not in ["open", "in_progress"]:
        flash("Only active tasks can be submitted for verification.", "danger")
        return redirect_for_task_with_anchor(task, return_anchor)

    progress = get_task_progress_row(task)
    task.status = "submitted"
    sync_progress_from_task(task, progress)
    db.session.commit()

    flash("Task submitted for verification.", "success")
    return redirect_for_task_with_anchor(task, return_anchor)


@mit_sts_bp.route("/tasks/<int:task_id>/verify", methods=["POST"])
@login_required
def verify_task(task_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    task = MITTask.query.get_or_404(task_id)
    progress = get_task_progress_row(task)
    return_anchor = request.form.get("return_anchor", "").strip()

    if task.status == "cancelled":
        flash("Cancelled tasks cannot be verified.", "danger")
        return redirect_for_task_with_anchor(task, return_anchor)

    task.status = "verified"
    sync_progress_from_task(task, progress)
    db.session.commit()

    flash("Task verified successfully.", "success")
    return redirect_for_task_with_anchor(task, return_anchor)


@mit_sts_bp.route("/tasks/<int:task_id>/send-back", methods=["POST"])
@login_required
def send_task_back(task_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    task = MITTask.query.get_or_404(task_id)
    progress = get_task_progress_row(task)
    return_anchor = request.form.get("return_anchor", "").strip()

    if task.status == "verified":
        task.completed_at = None

    task.status = "in_progress"
    sync_progress_from_task(task, progress)
    db.session.commit()

    flash("Task sent back to MIT.", "success")
    return redirect_for_task_with_anchor(task, return_anchor)