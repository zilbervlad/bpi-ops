from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.models import User
from app.extensions import db
from app.services.email_service import send_email

auth_bp = Blueprint("auth", __name__)


VALID_ROLES = {
    "admin",
    "supervisor",
    "general_manager",
    "manager",
    "tm",
    "maintenance",
}

STORE_REQUIRED_ROLES = {
    "general_manager",
    "manager",
    "tm",
}

AREA_REQUIRED_ROLES = {
    "supervisor",
}

ROLE_LABELS = {
    "admin": "Admin",
    "supervisor": "Supervisor",
    "general_manager": "General Manager",
    "manager": "Manager",
    "tm": "TM",
    "maintenance": "Maintenance",
}


def get_access_role(user):
    """
    Compatibility layer for the live app.

    General Managers are a separate database role, but for now they inherit
    the existing manager access path so current manager tools keep working.
    Existing routes throughout the app already check session["user_role"] == "manager".
    """
    if user.role == "general_manager":
        return "manager"
    return user.role


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
            account_role = session.get("account_role", user_role)

            # General Manager currently inherits Manager permissions.
            effective_allowed_roles = set(allowed_roles)
            if "manager" in effective_allowed_roles:
                effective_allowed_roles.add("general_manager")

            if user_role not in effective_allowed_roles and account_role not in effective_allowed_roles:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("dashboard.home"))

            return view(*args, **kwargs)
        return wrapped_view
    return decorator


def clean_access_fields(role, area_name, store_number):
    """
    Normalizes area/store assignment by role.

    Admin/Maintenance: no area or store
    Supervisor: area only
    General Manager/Manager/TM: store only
    """
    if role in {"admin", "maintenance"}:
        return None, None

    if role == "supervisor":
        return area_name, None

    if role in STORE_REQUIRED_ROLES:
        return None, store_number

    return area_name, store_number


def validate_user_access(role, area_name, store_number):
    if role not in VALID_ROLES:
        return False, "Please select a valid role."

    if role in AREA_REQUIRED_ROLES and not area_name:
        return False, "Supervisors must have an area assigned."

    if role in STORE_REQUIRED_ROLES and not store_number:
        return False, "General Managers, Managers, and TM accounts must have a store assigned."

    return True, None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username, is_active=True).first()

        if user and user.check_password(password):
            session.permanent = True

            access_role = get_access_role(user)

            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_role"] = access_role
            session["account_role"] = user.role
            session["role_label"] = ROLE_LABELS.get(user.role, user.role.title())
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
            email = request.form.get("email", "").strip() or None
            notification_email = request.form.get("notification_email", "").strip() or None
            email_enabled = request.form.get("email_enabled") == "on"

            is_valid, error_message = validate_user_access(role, area_name, store_number)
            if not name or not username or not password or not is_valid:
                flash(error_message or "Please complete all required fields correctly.", "error")
                return redirect(url_for("auth.manage_users"))

            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash("That username already exists.", "error")
                return redirect(url_for("auth.manage_users"))

            area_name, store_number = clean_access_fields(role, area_name, store_number)

            user = User(
                name=name,
                username=username,
                role=role,
                area_name=area_name,
                store_number=store_number,
                email=email,
                notification_email=notification_email,
                email_enabled=email_enabled,
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
            email = request.form.get("email", "").strip() or None
            notification_email = request.form.get("notification_email", "").strip() or None
            email_enabled = request.form.get("email_enabled") == "on"
            new_password = request.form.get("password", "").strip()

            # -------------------------
            # PROTECTED ADMIN LOGIC
            # -------------------------
            if user.role == "admin":
                user.email = email
                user.notification_email = notification_email
                user.email_enabled = email_enabled

                if new_password:
                    user.set_password(new_password)
                    db.session.commit()
                    flash("Admin email settings and password updated successfully.", "success")
                else:
                    db.session.commit()
                    flash("Admin email settings updated successfully.", "success")

                return redirect(url_for("auth.manage_users"))

            is_valid, error_message = validate_user_access(role, area_name, store_number)
            if not name or not username or not is_valid:
                flash(error_message or "Please complete all required fields correctly.", "error")
                return redirect(url_for("auth.manage_users"))

            existing_user = User.query.filter(
                User.username == username,
                User.id != user.id
            ).first()
            if existing_user:
                flash("That username already exists.", "error")
                return redirect(url_for("auth.manage_users"))

            area_name, store_number = clean_access_fields(role, area_name, store_number)

            user.name = name
            user.username = username
            user.role = role
            user.area_name = area_name
            user.store_number = store_number
            user.email = email
            user.notification_email = notification_email
            user.email_enabled = email_enabled

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
    return render_template("users.html", users=users, role_labels=ROLE_LABELS)


@auth_bp.route("/users/<int:user_id>/send-test-email", methods=["POST"])
@login_required
@role_required("admin")
def send_test_email_to_user(user_id):
    user = User.query.get_or_404(user_id)

    to_email = user.get_notification_email()
    if not to_email:
        flash("This user does not have an email address configured for notifications.", "error")
        return redirect(url_for("auth.manage_users"))

    try:
        send_email(
            to_email=to_email,
            subject="BPI Ops Test Email",
            body=(
                f"Hello {user.name},\n\n"
                "This is a test email from BPI Ops.\n\n"
                "If you received this, your email settings are working."
            ),
        )
        flash(f"Test email sent to {to_email}.", "success")
    except Exception as e:
        flash(f"Failed to send test email: {str(e)}", "error")

    return redirect(url_for("auth.manage_users"))
