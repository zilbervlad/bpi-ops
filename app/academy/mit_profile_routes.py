from .routes_shared import *

@mit_sts_bp.route("/list")
@login_required
def list_mits():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    q = request.args.get("q", "").strip()
    store = request.args.get("store", "").strip()
    level = request.args.get("level", "").strip()
    status = request.args.get("status", "").strip()
    coach = request.args.get("coach", "").strip()
    task_filter = request.args.get("task_filter", "").strip()

    query = (
        MITProfile.query
        .join(User, MITProfile.user_id == User.id)
        .filter(User.is_active == True)
    )

    if q:
        query = query.filter(User.name.ilike(f"%{q}%"))

    if store:
        query = query.filter(MITProfile.store_number == store)

    if level:
        try:
            query = query.filter(MITProfile.current_level == int(level))
        except ValueError:
            pass

    if status:
        query = query.filter(MITProfile.sts_status == status)

    if coach:
        try:
            query = query.filter(MITProfile.coach_user_id == int(coach))
        except ValueError:
            pass

    mits = query.all()

    progress_map = {}
    task_counts_map = {}

    filtered_mits = []
    total_overdue = 0
    total_open = 0
    total_submitted = 0

    for mit in mits:
        current_level = getattr(mit, "current_level", 1) or 1
        progress_map[mit.id] = calculate_level_progress(mit.id, current_level)

        open_count, overdue_count, submitted_count = get_task_counts(mit.id)
        task_counts_map[mit.id] = {
            "open": open_count,
            "overdue": overdue_count,
            "submitted": submitted_count,
        }

        total_overdue += overdue_count
        total_open += open_count
        total_submitted += submitted_count

        include = True
        if task_filter == "open" and open_count == 0:
            include = False
        elif task_filter == "overdue" and overdue_count == 0:
            include = False
        elif task_filter == "submitted" and submitted_count == 0:
            include = False

        if include:
            filtered_mits.append(mit)

    mits = filtered_mits

    stores = [
        row[0]
        for row in db.session.query(MITProfile.store_number)
        .filter(MITProfile.store_number.isnot(None), MITProfile.store_number != "")
        .distinct()
        .order_by(MITProfile.store_number.asc())
        .all()
    ]

    coaches = User.query.filter(
        User.role.in_(["admin", "supervisor"])
    ).order_by(User.name.asc()).all()

    if total_overdue > 0:
        doughy_message = f"You have {total_overdue} overdue task{'s' if total_overdue != 1 else ''}. Start there first."
    elif total_submitted > 0:
        doughy_message = f"{total_submitted} task{'s' if total_submitted != 1 else ''} waiting for review."
    elif total_open > 0:
        doughy_message = f"{total_open} open task{'s' if total_open != 1 else ''} in progress."
    else:
        doughy_message = "All MIT tasks are clean. Time to assign new work."

    return render_template(
        "academy/mit_sts/mit_list.html",
        mits=mits,
        progress_map=progress_map,
        task_counts_map=task_counts_map,
        stores=stores,
        coaches=coaches,
        q=q,
        selected_store=store,
        selected_level=level,
        selected_status=status,
        selected_coach=coach,
        selected_task_filter=task_filter,
        doughy_message=doughy_message,
        user=current_user,
    )

@mit_sts_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_mit():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    users = User.query.order_by(User.name.asc()).all()
    coaches = User.query.filter(
        User.role.in_(["admin", "supervisor"])
    ).order_by(User.name.asc()).all()

    if request.method == "POST":
        user_source = request.form.get("user_source", "existing").strip()

        if user_source == "new":
            flash(
                "Create the employee in BPI Ops Users first, then assign their MIT profile here.",
                "warning",
            )
            return redirect(url_for("mit_sts.new_mit"))
        store_number = request.form.get("store_number", "").strip()
        coach_user_id = request.form.get("coach_user_id", "").strip()
        current_level = request.form.get("current_level", "1").strip()
        start_date = request.form.get("start_date", "").strip()
        sts_status = request.form.get("sts_status", "on_track").strip()
        next_review_date = request.form.get("next_review_date", "").strip()
        notes = request.form.get("notes", "").strip()

        user = None

        if user_source == "new":
            new_name = request.form.get("new_name", "").strip()
            new_username = request.form.get("new_username", "").strip()
            new_password = request.form.get("new_password", "").strip()

            if not new_name or not new_username or not new_password:
                flash("New MIT name, username, and temporary password are required.", "danger")
                return render_template(
                    "academy/mit_sts/mit_form.html",
                    page_title="Create MIT Profile",
                    submit_label="Create MIT Profile",
                    mit=None,
                    users=users,
                    coaches=coaches,
                    user=current_user,
                )

            existing_user = User.query.filter_by(username=new_username).first()
            if existing_user:
                flash("That username already exists.", "danger")
                return render_template(
                    "academy/mit_sts/mit_form.html",
                    page_title="Create MIT Profile",
                    submit_label="Create MIT Profile",
                    mit=None,
                    users=users,
                    coaches=coaches,
                    user=current_user,
                )

            user = User(
                name=new_name,
                username=new_username,
                role="mit",
                store_number=store_number or None,
                is_active=True,
            )
            user.set_password(new_password)
            db.session.add(user)
            db.session.flush()

        else:
            user_id = request.form.get("user_id", "").strip()

            if not user_id:
                flash("MIT user is required.", "danger")
                return render_template(
                    "academy/mit_sts/mit_form.html",
                    page_title="Create MIT Profile",
                    submit_label="Create MIT Profile",
                    mit=None,
                    users=users,
                    coaches=coaches,
                    user=current_user,
                )

            user = User.query.get(int(user_id))
            if not user:
                flash("Selected user was not found.", "danger")
                return render_template(
                    "academy/mit_sts/mit_form.html",
                    page_title="Create MIT Profile",
                    submit_label="Create MIT Profile",
                    mit=None,
                    users=users,
                    coaches=coaches,
                    user=current_user,
                )

        existing_profile = MITProfile.query.filter_by(user_id=user.id).first()
        if existing_profile:
            flash("This user already has an MIT STS profile.", "danger")
            return render_template(
                "academy/mit_sts/mit_form.html",
                page_title="Create MIT Profile",
                submit_label="Create MIT Profile",
                mit=None,
                users=users,
                coaches=coaches,
                user=current_user,
            )

        try:
            current_level_int = int(current_level)
        except ValueError:
            current_level_int = 1

        start_date_obj = None
        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        next_review_date_obj = None
        if next_review_date:
            try:
                next_review_date_obj = datetime.strptime(next_review_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        if should_force_mit_role(user):
            user.role = "mit"
        if store_number:
            user.store_number = store_number

        mit = MITProfile(
            user_id=user.id,
            store_number=store_number or None,
            coach_user_id=int(coach_user_id) if coach_user_id else None,
            current_level=current_level_int,
            target_level=get_target_level(current_level_int),
            start_date=start_date_obj,
            sts_status=sts_status or "on_track",
            next_review_date=next_review_date_obj,
            notes=notes or None,
        )

        db.session.add(mit)
        db.session.commit()

        ensure_progress_rows_for_mit(mit)

        flash("MIT profile created successfully.", "success")
        return redirect(url_for("mit_sts.view_mit", mit_id=mit.id))

    return render_template(
        "academy/mit_sts/mit_form.html",
        page_title="Create MIT Profile",
        submit_label="Create MIT Profile",
        mit=None,
        users=users,
        coaches=coaches,
        user=current_user,
    )

@mit_sts_bp.route("/my")
@login_required
def my_mit():
    profile = MITProfile.query.filter_by(user_id=current_user.id).first()

    if profile:
        return redirect(url_for("mit_sts.view_mit", mit_id=profile.id))

    if is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    flash("No MIT profile found.", "danger")
    return redirect(url_for("auth.logout"))

@mit_sts_bp.route("/<int:mit_id>")
@login_required
def view_mit(mit_id):
    profile = MITProfile.query.get_or_404(mit_id)

    if not user_can_access_mit_profile(profile):
        return redirect(url_for("mit_sts.dashboard"))

    ensure_progress_rows_for_mit(profile)

    level_1_progress = calculate_level_progress(profile.id, 1)
    level_2_progress = calculate_level_progress(profile.id, 2)
    level_3_progress = calculate_level_progress(profile.id, 3)

    overall_progress = 0
    all_templates = MITLevelTemplate.query.all()
    if all_templates:
        all_template_ids = [item.id for item in all_templates]
        completed_total = MITLevelProgress.query.filter(
            MITLevelProgress.mit_profile_id == profile.id,
            MITLevelProgress.template_item_id.in_(all_template_ids),
            MITLevelProgress.status == "complete",
        ).count()
        overall_progress = round((completed_total / len(all_templates)) * 100)

    incomplete_count = MITLevelProgress.query.join(
        MITLevelTemplate,
        MITLevelProgress.template_item_id == MITLevelTemplate.id
    ).filter(
        MITLevelProgress.mit_profile_id == profile.id,
        MITLevelTemplate.level_number == profile.current_level,
        MITLevelProgress.status != "complete"
    ).count()

    open_tasks_count, overdue_tasks_count, submitted_tasks_count = get_task_counts(profile.id)

    promotions = MITPromotion.query.filter_by(
        mit_profile_id=profile.id
    ).order_by(MITPromotion.effective_date.asc()).all()

    today = date.today()

    level_durations = {
        1: None,
        2: None,
        3: None,
    }

    level_timeline = {
        1: {"start": profile.start_date, "end": None, "status": "Not Reached"},
        2: {"start": None, "end": None, "status": "Not Reached"},
        3: {"start": None, "end": None, "status": "Not Reached"},
    }

    level_2_date = None
    level_3_date = None

    for promotion in promotions:
        to_level = str(promotion.to_level).strip().lower() if promotion.to_level is not None else ""
        if to_level == "2" and not level_2_date:
            level_2_date = promotion.effective_date
        elif to_level == "3" and not level_3_date:
            level_3_date = promotion.effective_date

    if profile.start_date:
        level_timeline[1]["start"] = profile.start_date

        if level_2_date:
            level_timeline[1]["end"] = level_2_date
            level_timeline[1]["status"] = "Complete"
            level_durations[1] = round((level_2_date - profile.start_date).days / 7, 1)
        elif profile.current_level == 1:
            level_timeline[1]["end"] = today
            level_timeline[1]["status"] = "In Progress"
            level_durations[1] = round((today - profile.start_date).days / 7, 1)

    if level_2_date:
        level_timeline[2]["start"] = level_2_date

        if level_3_date:
            level_timeline[2]["end"] = level_3_date
            level_timeline[2]["status"] = "Complete"
            level_durations[2] = round((level_3_date - level_2_date).days / 7, 1)
        elif profile.current_level == 2:
            level_timeline[2]["end"] = today
            level_timeline[2]["status"] = "In Progress"
            level_durations[2] = round((today - level_2_date).days / 7, 1)

    if level_3_date:
        level_timeline[3]["start"] = level_3_date

        if profile.current_level == 3:
            level_timeline[3]["end"] = today
            level_timeline[3]["status"] = "In Progress"
            level_durations[3] = round((today - level_3_date).days / 7, 1)

    return render_template(
        "academy/mit_sts/mit_detail.html",
        mit=profile,
        profile=profile,
        level_1_progress=level_1_progress,
        level_2_progress=level_2_progress,
        level_3_progress=level_3_progress,
        overall_progress=overall_progress,
        incomplete_count=incomplete_count,
        open_tasks_count=open_tasks_count,
        overdue_tasks_count=overdue_tasks_count,
        submitted_tasks_count=submitted_tasks_count,
        promotions=promotions,
        level_durations=level_durations,
        level_timeline=level_timeline,
        user=current_user,
        can_edit=is_coach(),
        can_manage_templates=is_coach(),
    )


@mit_sts_bp.route("/<int:mit_id>/edit", methods=["GET", "POST"])
@login_required
def edit_mit(mit_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    mit = MITProfile.query.get_or_404(mit_id)
    users = User.query.order_by(User.name.asc()).all()
    coaches = User.query.filter(
        User.role.in_(["admin", "supervisor"])
    ).order_by(User.name.asc()).all()

    if request.method == "POST":
        user_id_raw = request.form.get("user_id", "").strip()
        if user_id_raw:
            target_user = User.query.get(int(user_id_raw))
            if target_user:
                mit.user_id = target_user.id
                if should_force_mit_role(target_user):
                    target_user.role = "mit"

        mit.store_number = request.form.get("store_number", "").strip() or None

        coach_user_id = request.form.get("coach_user_id", "").strip()
        mit.coach_user_id = int(coach_user_id) if coach_user_id else None

        try:
            mit.current_level = int(request.form.get("current_level", mit.current_level))
        except ValueError:
            pass

        mit.target_level = get_target_level(mit.current_level)
        mit.sts_status = request.form.get("sts_status", mit.sts_status).strip() or mit.sts_status
        mit.notes = request.form.get("notes", "").strip() or None

        start_date = request.form.get("start_date", "").strip()
        if start_date:
            try:
                mit.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                pass
        else:
            mit.start_date = None

        next_review_date = request.form.get("next_review_date", "").strip()
        if next_review_date:
            try:
                mit.next_review_date = datetime.strptime(next_review_date, "%Y-%m-%d").date()
            except ValueError:
                pass
        else:
            mit.next_review_date = None

        if mit.mit_user and mit.store_number:
            mit.mit_user.store_number = mit.store_number

        db.session.commit()

        flash("MIT profile updated successfully.", "success")
        return redirect(url_for("mit_sts.view_mit", mit_id=mit.id))

    return render_template(
        "academy/mit_sts/mit_form.html",
        page_title="Edit MIT Profile",
        submit_label="Save Changes",
        mit=mit,
        users=users,
        coaches=coaches,
        user=current_user,
    )
