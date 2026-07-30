from .routes_shared import *

@mit_sts_bp.route("/users")
@login_required
def list_users():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    q = request.args.get("q", "").strip()
    selected_role = request.args.get("role", "").strip()
    selected_store = request.args.get("store", "").strip()

    query = User.query

    if q:
        query = query.filter(
            db.or_(
                User.name.ilike(f"%{q}%"),
                User.username.ilike(f"%{q}%")
            )
        )

    if selected_role:
        query = query.filter(User.role == selected_role)

    if selected_store:
        query = query.filter(User.store_number == selected_store)

    users = query.order_by(User.name.asc()).all()

    return render_template(
        "academy/mit_sts/users.html",
        users=users,
        q=q,
        selected_role=selected_role,
        selected_store=selected_store,
        roles=available_user_roles(),
        user=current_user,
    )


@mit_sts_bp.route("/users/<int:user_id>")
@login_required
def view_user(user_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    user_item = User.query.get_or_404(user_id)
    mit_profile = user_item.mit_profiles[0] if user_item.mit_profiles else None

    return render_template(
        "academy/mit_sts/user_detail.html",
        user_item=user_item,
        mit_profile=mit_profile,
        user=current_user,
    )


@mit_sts_bp.route("/users/new", methods=["GET", "POST"])
@login_required
def new_user():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "mit").strip()
        store_number = request.form.get("store_number", "").strip()
        password = request.form.get("password", "").strip()
        is_active_user = request.form.get("is_active_user") == "1"

        if not name or not username or not password:
            flash("Name, username, and password are required.", "danger")
            return render_template(
                "academy/mit_sts/user_form.html",
                page_title="Create User",
                submit_label="Create User",
                user_item=None,
                roles=available_user_roles(),
                user=current_user,
            )

        if role not in available_user_roles():
            role = "mit"

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("That username already exists.", "danger")
            return render_template(
                "academy/mit_sts/user_form.html",
                page_title="Create User",
                submit_label="Create User",
                user_item=None,
                roles=available_user_roles(),
                user=current_user,
            )

        user_item = User(
            name=name,
            username=username,
            role=role,
            store_number=store_number or None,
            is_active_user=is_active_user,
        )
        user_item.set_password(password)

        db.session.add(user_item)
        db.session.commit()

        flash("User created successfully.", "success")
        return redirect(url_for("mit_sts.list_users"))

    return render_template(
        "academy/mit_sts/user_form.html",
        page_title="Create User",
        submit_label="Create User",
        user_item=None,
        roles=available_user_roles(),
        user=current_user,
    )


@mit_sts_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    user_item = User.query.get_or_404(user_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "mit").strip()
        store_number = request.form.get("store_number", "").strip()
        password = request.form.get("password", "").strip()
        is_active_user = request.form.get("is_active_user") == "1"

        if not name or not username:
            flash("Name and username are required.", "danger")
            return render_template(
                "academy/mit_sts/user_form.html",
                page_title="Edit User",
                submit_label="Save Changes",
                user_item=user_item,
                roles=available_user_roles(),
                user=current_user,
            )

        existing = User.query.filter(User.username == username, User.id != user_item.id).first()
        if existing:
            flash("That username already exists.", "danger")
            return render_template(
                "academy/mit_sts/user_form.html",
                page_title="Edit User",
                submit_label="Save Changes",
                user_item=user_item,
                roles=available_user_roles(),
                user=current_user,
            )

        if role not in available_user_roles():
            role = "mit"

        user_item.name = name
        user_item.username = username
        user_item.role = role
        user_item.store_number = store_number or None
        user_item.is_active_user = is_active_user

        if password:
            user_item.set_password(password)

        linked_mit = user_item.mit_profiles[0] if user_item.mit_profiles else None
        if linked_mit and store_number:
            linked_mit.store_number = store_number

        db.session.commit()

        flash("User updated successfully.", "success")
        return redirect(url_for("mit_sts.view_user", user_id=user_item.id))

    return render_template(
        "academy/mit_sts/user_form.html",
        page_title="Edit User",
        submit_label="Save Changes",
        user_item=user_item,
        roles=available_user_roles(),
        user=current_user,
    )
