from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.models import User
from app.extensions import db

auth_bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped_view


def role_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("auth.login"))

            user_role = session.get("user_role")
            if user_role not in allowed_roles:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("dashboard.home"))

            return view(*args, **kwargs)
        return wrapped_view
    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username, is_active=True).first()

        if user and user.check_password(password):
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_role"] = user.role
            session["user_area"] = user.area_name
            session["user_store"] = user.store_number
            return redirect(url_for("dashboard.home"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/users", methods=["GET", "POST"])
@login_required
@role_required("admin")
def manage_users():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        # -------------------------
        # CREATE USER
        # -------------------------
        if action == "create":
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            role = request.form.get("role", "").strip()
            area_name = request.form.get("area_name", "").strip() or None
            store_number = request.form.get("store_number", "").strip() or None

            valid_roles = {"admin", "supervisor", "manager", "maintenance"}

            if not name or not username or not password or role not in valid_roles:
                flash("Please complete all required fields correctly.", "error")
                return redirect(url_for("auth.manage_users"))

            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash("That username already exists.", "error")
                return redirect(url_for("auth.manage_users"))

            if role == "supervisor" and not area_name:
                flash("Supervisors must have an area assigned.", "error")
                return redirect(url_for("auth.manage_users"))

            if role == "manager" and not store_number:
                flash("Managers must have a store assigned.", "error")
                return redirect(url_for("auth.manage_users"))

            if role in {"admin", "maintenance"}:
                area_name = None
                store_number = None

            if role == "supervisor":
                store_number = None

            if role == "manager":
                area_name = None

            user = User(
                name=name,
                username=username,
                role=role,
                area_name=area_name,
                store_number=store_number,
                is_active=True,
            )
            user.set_password(password)

            db.session.add(user)
            db.session.commit()

            flash("User created successfully.", "success")
            return redirect(url_for("auth.manage_users"))

        # -------------------------
        # UPDATE USER
        # -------------------------
        if action == "update":
            user_id = request.form.get("user_id", "").strip()
            user = User.query.get(user_id)

            if not user:
                flash("User not found.", "error")
                return redirect(url_for("auth.manage_users"))

            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "").strip()
            area_name = request.form.get("area_name", "").strip() or None
            store_number = request.form.get("store_number", "").strip() or None
            new_password = request.form.get("password", "").strip()

            valid_roles = {"admin", "supervisor", "manager", "maintenance"}

            # -------------------------
            # PROTECTED ADMIN LOGIC
            # -------------------------
            if user.role == "admin":
                if not new_password:
                    flash("For protected admin users, only password reset is allowed here.", "error")
                    return redirect(url_for("auth.manage_users"))

                user.set_password(new_password)
                db.session.commit()

                flash("Admin password updated successfully.", "success")
                return redirect(url_for("auth.manage_users"))

            if not name or not username or role not in valid_roles:
                flash("Please complete all required fields correctly.", "error")
                return redirect(url_for("auth.manage_users"))

            existing_user = User.query.filter(
                User.username == username,
                User.id != user.id
            ).first()
            if existing_user:
                flash("That username already exists.", "error")
                return redirect(url_for("auth.manage_users"))

            if role == "supervisor" and not area_name:
                flash("Supervisors must have an area assigned.", "error")
                return redirect(url_for("auth.manage_users"))

            if role == "manager" and not store_number:
                flash("Managers must have a store assigned.", "error")
                return redirect(url_for("auth.manage_users"))

            if role in {"admin", "maintenance"}:
                area_name = None
                store_number = None

            if role == "supervisor":
                store_number = None

            if role == "manager":
                area_name = None

            user.name = name
            user.username = username
            user.role = role
            user.area_name = area_name
            user.store_number = store_number

            if new_password:
                user.set_password(new_password)

            db.session.commit()
            flash("User updated successfully.", "success")
            return redirect(url_for("auth.manage_users"))

        # -------------------------
        # DEACTIVATE USER
        # -------------------------
        if action == "deactivate":
            user_id = request.form.get("user_id", "").strip()
            user = User.query.get(user_id)

            if not user:
                flash("User not found.", "error")
                return redirect(url_for("auth.manage_users"))

            if user.role == "admin":
                flash("Admin users cannot be deactivated here.", "error")
                return redirect(url_for("auth.manage_users"))

            user.is_active = False
            db.session.commit()

            flash("User deactivated.", "success")
            return redirect(url_for("auth.manage_users"))

        # -------------------------
        # REACTIVATE USER
        # -------------------------
        if action == "activate":
            user_id = request.form.get("user_id", "").strip()
            user = User.query.get(user_id)

            if not user:
                flash("User not found.", "error")
                return redirect(url_for("auth.manage_users"))

            user.is_active = True
            db.session.commit()

            flash("User activated.", "success")
            return redirect(url_for("auth.manage_users"))

    users = User.query.order_by(User.name.asc()).all()
    return render_template("users.html", users=users)