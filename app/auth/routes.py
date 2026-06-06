from datetime import datetime
import os
import base64
from io import BytesIO

import qrcode
import requests
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash
from app.models import User, Store, PendingRegistrationRequest
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
    "hr",
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
    "hr": "HR",
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


def get_current_account_role():
    return session.get("account_role", session.get("user_role"))


def current_user_is_admin():
    return get_current_account_role() == "admin"


def current_user_is_general_manager():
    return get_current_account_role() == "general_manager"


def current_user_store():
    return session.get("user_store")


def current_user_is_supervisor():
    return get_current_account_role() == "supervisor"



def sync_registration_user_to_bpi_connect(user, registration, final_role):
    api_base = os.getenv("BPI_CONNECT_API_BASE", "").strip().rstrip("/")
    integration_secret = os.getenv("BPI_CONNECT_INTEGRATION_SECRET", "").strip()

    if not api_base or not integration_secret:
        return {
            "success": False,
            "skipped": True,
            "error": "BPI Connect integration is not configured.",
        }

    payload = {
        "bpi_ops_user_id": user.id,
        "name": user.name,
        "email": user.email,
        "role": final_role,
        "position": registration.requested_position,
        "store_number": user.store_number or registration.store_number,
        "area": getattr(user, "area_name", None),
        "is_active": bool(user.is_active),
        "send_invite": True,
    }

    try:
        response = requests.post(
            f"{api_base}/api/integrations/bpi-ops/users/sync",
            json=payload,
            headers={
                "X-BPI-Ops-Secret": integration_secret,
            },
            timeout=5,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        return {
            "success": response.status_code < 400 and bool(data.get("success")),
            "status_code": response.status_code,
            "data": data,
            "error": None if response.status_code < 400 else data,
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error),
        }


def current_user_can_review_registration_requests():
    return get_current_account_role() in {"admin", "supervisor", "general_manager"}


def registration_visible_store_numbers():
    role = get_current_account_role()

    if role == "admin":
        return None

    if role == "supervisor":
        area_name = session.get("user_area")
        if not area_name:
            return set()
        return {
            store.store_number
            for store in Store.query.filter_by(area_name=area_name, is_active=True).all()
        }

    if role == "general_manager":
        store_number = current_user_store()
        return {store_number} if store_number else set()

    return set()


def can_review_registration_request(registration):
    visible_stores = registration_visible_store_numbers()
    if visible_stores is None:
        return True
    return registration.store_number in visible_stores


def allowed_registration_approval_roles():
    role = get_current_account_role()

    if role == "admin":
        return ["tm", "manager", "general_manager", "supervisor", "maintenance", "hr"]

    if role == "supervisor":
        return ["tm", "manager", "general_manager"]

    if role == "general_manager":
        return ["tm"]

    return []


def registration_status_counts(registrations):
    counts = {"pending": 0, "approved": 0, "rejected": 0}

    for registration in registrations:
        if registration.status in counts:
            counts[registration.status] += 1

    return counts



def make_registration_qr_data_uri(target_url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(target_url)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def visible_registration_qr_stores():
    visible_stores = registration_visible_store_numbers()

    query = Store.query.filter_by(is_active=True)

    if visible_stores is not None:
        if not visible_stores:
            return []
        query = query.filter(Store.store_number.in_(visible_stores))

    return query.order_by(Store.store_number.asc()).all()



def user_is_tm_in_current_gm_store(user):
    return (
        current_user_is_general_manager()
        and user
        and user.role == "tm"
        and user.store_number == current_user_store()
    )


def current_user_can_manage_target_user(user):
    if current_user_is_admin():
        return True

    if current_user_is_general_manager():
        return user_is_tm_in_current_gm_store(user)

    return False



def get_password_reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def make_password_reset_token(user):
    serializer = get_password_reset_serializer()
    return serializer.dumps(
        {"user_id": user.id},
        salt="bpi-ops-password-reset",
    )


def load_password_reset_user(token, max_age=3600):
    serializer = get_password_reset_serializer()
    data = serializer.loads(
        token,
        salt="bpi-ops-password-reset",
        max_age=max_age,
    )

    user_id = data.get("user_id")
    if not user_id:
        return None

    return User.query.filter_by(id=user_id, is_active=True).first()


def find_password_reset_user(identifier):
    identifier = (identifier or "").strip()

    if not identifier:
        return None

    query = User.query.filter(User.is_active == True)

    return query.filter(
        db.or_(
            User.username.ilike(identifier),
            User.email.ilike(identifier),
            User.notification_email.ilike(identifier),
        )
    ).first()


def send_password_reset_email(user):
    to_email = user.get_notification_email()

    if not to_email:
        return False

    token = make_password_reset_token(user)
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    send_email(
        to_email=to_email,
        subject="Reset your BPI Ops password",
        body=(
            f"Hello {user.name},\n\n"
            "We received a request to reset your BPI Ops password.\n\n"
            f"Reset your password here:\n{reset_url}\n\n"
            "This link expires in 1 hour.\n\n"
            "If you did not request this, you can ignore this email."
        ),
    )

    return True


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


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        user = find_password_reset_user(identifier)

        if user:
            try:
                send_password_reset_email(user)
            except Exception:
                # Do not expose account/email configuration details publicly.
                pass

        flash("If we found that account, we sent a password reset link.", "success")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        user = load_password_reset_user(token)
    except SignatureExpired:
        flash("That password reset link expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("That password reset link is invalid. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if not user:
        flash("That password reset link is invalid. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not password:
            flash("Please enter a new password.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        user.set_password(password)
        db.session.commit()

        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)


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
@role_required("admin", "general_manager")
def manage_users():
    can_manage_all_users = current_user_is_admin()
    gm_store = current_user_store()

    if current_user_is_general_manager() and not gm_store:
        flash("Your General Manager account does not have a store assigned.", "error")
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        # =========================================================
        # GENERAL MANAGER USER MANAGEMENT
        # GMs may only create/edit/reactivate/deactivate TM accounts
        # for their own assigned store.
        # =========================================================
        if current_user_is_general_manager():
            if action == "create":
                name = request.form.get("name", "").strip()
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "").strip()
                email = request.form.get("email", "").strip() or None
                notification_email = request.form.get("notification_email", "").strip() or None
                position = request.form.get("position", "").strip() or None
                email_enabled = request.form.get("email_enabled") == "on"

                if not name or not username or not password:
                    flash("Please complete all required fields.", "error")
                    return redirect(url_for("auth.manage_users"))

                existing_user = User.query.filter_by(username=username).first()
                if existing_user:
                    flash("That username already exists.", "error")
                    return redirect(url_for("auth.manage_users"))

                user = User(
                    name=name,
                    username=username,
                    role="tm",
                    position=position,
                    area_name=None,
                    store_number=gm_store,
                    email=email,
                    notification_email=notification_email,
                    email_enabled=email_enabled,
                    is_active=True,
                )
                user.set_password(password)

                db.session.add(user)
                db.session.commit()

                flash(f"TM account created for store {gm_store}.", "success")
                return redirect(url_for("auth.manage_users"))

            if action == "update":
                user_id = request.form.get("user_id", "").strip()
                user = User.query.get(user_id)

                if not user_is_tm_in_current_gm_store(user):
                    flash("You can only update TM accounts assigned to your store.", "error")
                    return redirect(url_for("auth.manage_users"))

                name = request.form.get("name", "").strip()
                username = request.form.get("username", "").strip()
                email = request.form.get("email", "").strip() or None
                notification_email = request.form.get("notification_email", "").strip() or None
                position = request.form.get("position", "").strip() or None
                email_enabled = request.form.get("email_enabled") == "on"
                new_password = request.form.get("password", "").strip()

                if not name or not username:
                    flash("Please complete all required fields.", "error")
                    return redirect(url_for("auth.manage_users"))

                existing_user = User.query.filter(
                    User.username == username,
                    User.id != user.id
                ).first()
                if existing_user:
                    flash("That username already exists.", "error")
                    return redirect(url_for("auth.manage_users"))

                user.name = name
                user.username = username
                user.role = "tm"
                user.position = position
                user.area_name = None
                user.store_number = gm_store
                user.email = email
                user.notification_email = notification_email
                user.email_enabled = email_enabled

                if new_password:
                    user.set_password(new_password)

                db.session.commit()

                flash("TM account updated successfully.", "success")
                return redirect(url_for("auth.manage_users"))

            if action == "deactivate":
                user_id = request.form.get("user_id", "").strip()
                user = User.query.get(user_id)

                if not user_is_tm_in_current_gm_store(user):
                    flash("You can only deactivate TM accounts assigned to your store.", "error")
                    return redirect(url_for("auth.manage_users"))

                user.is_active = False
                db.session.commit()

                flash("TM account deactivated.", "success")
                return redirect(url_for("auth.manage_users"))

            if action == "activate":
                user_id = request.form.get("user_id", "").strip()
                user = User.query.get(user_id)

                if not user_is_tm_in_current_gm_store(user):
                    flash("You can only activate TM accounts assigned to your store.", "error")
                    return redirect(url_for("auth.manage_users"))

                user.is_active = True
                db.session.commit()

                flash("TM account activated.", "success")
                return redirect(url_for("auth.manage_users"))

            flash("Invalid action.", "error")
            return redirect(url_for("auth.manage_users"))

        # =========================================================
        # ADMIN USER MANAGEMENT
        # Admins can manage all account types.
        # =========================================================

        # -------------------------
        # CREATE USER
        # -------------------------
        if action == "create":
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            role = request.form.get("role", "").strip()
            position = request.form.get("position", "").strip() or None
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
                position=position,
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
            position = request.form.get("position", "").strip() or None
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
            user.position = position
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

    if current_user_is_general_manager():
        users = User.query.filter_by(
            role="tm",
            store_number=gm_store
        ).order_by(User.name.asc()).all()
    else:
        users = User.query.order_by(User.name.asc()).all()

    return render_template(
        "users.html",
        users=users,
        role_labels=ROLE_LABELS,
        can_manage_all_users=can_manage_all_users,
        gm_store=gm_store,
    )


@auth_bp.route("/users/<int:user_id>/send-test-email", methods=["POST"])
@login_required
@role_required("admin", "general_manager")
def send_test_email_to_user(user_id):
    user = User.query.get_or_404(user_id)

    if not current_user_can_manage_target_user(user):
        flash("You do not have permission to email that user.", "error")
        return redirect(url_for("auth.manage_users"))

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




@auth_bp.route("/users/registration-qr/print")
@login_required
@role_required("admin", "supervisor", "general_manager")
def registration_qr_print():
    if not current_user_can_review_registration_requests():
        flash("You do not have permission to print registration QR codes.", "error")
        return redirect(url_for("dashboard.home"))

    stores = visible_registration_qr_stores()
    allowed_store_numbers = {store.store_number for store in stores}

    selected_store = (request.args.get("store") or "").strip()

    if selected_store not in allowed_store_numbers:
        flash("You do not have access to that store QR.", "error")
        return redirect(url_for("auth.registration_qr_center"))

    selected_store_obj = next(
        (store for store in stores if store.store_number == selected_store),
        None,
    )

    register_url = url_for("auth.public_register", store=selected_store, _external=True)

    return render_template(
        "registration_qr_print.html",
        selected_store=selected_store,
        selected_store_name=selected_store_obj.store_name if selected_store_obj else "",
        register_url=register_url,
        qr_data_uri=make_registration_qr_data_uri(register_url),
    )


@auth_bp.route("/users/registration-qr")
@login_required
@role_required("admin", "supervisor", "general_manager")
def registration_qr_center():
    if not current_user_can_review_registration_requests():
        flash("You do not have permission to access registration QR codes.", "error")
        return redirect(url_for("dashboard.home"))

    stores = visible_registration_qr_stores()
    account_role = get_current_account_role()

    selected_store = (request.args.get("store") or "").strip()

    allowed_store_numbers = {store.store_number for store in stores}
    if selected_store not in allowed_store_numbers:
        selected_store = stores[0].store_number if stores else ""

    company_register_url = url_for("auth.public_register", _external=True)
    selected_store_url = (
        url_for("auth.public_register", store=selected_store, _external=True)
        if selected_store
        else company_register_url
    )

    return render_template(
        "registration_qr_center.html",
        stores=stores,
        selected_store=selected_store,
        company_register_url=company_register_url,
        company_qr=make_registration_qr_data_uri(company_register_url),
        selected_store_url=selected_store_url,
        selected_store_qr=make_registration_qr_data_uri(selected_store_url),
        show_company_qr=(account_role == "admin"),
    )


@auth_bp.route("/public/register", methods=["GET", "POST"])
def public_register():
    store_number = (request.args.get("store") or request.form.get("store_number") or "").strip()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        requested_position = request.form.get("requested_position", "").strip() or None
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not full_name or not username or not store_number or not password:
            flash("Please complete name, username, store, and password.", "error")
            return render_template(
                "public_register.html",
                store_number=store_number,
                requested_position=requested_position,
            )

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template(
                "public_register.html",
                store_number=store_number,
                requested_position=requested_position,
            )

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("That username already exists. Please choose another username.", "error")
            return render_template(
                "public_register.html",
                store_number=store_number,
                requested_position=requested_position,
            )

        existing_pending = PendingRegistrationRequest.query.filter_by(
            username=username,
            status="pending",
        ).first()
        if existing_pending:
            flash("A pending request already exists for that username.", "error")
            return render_template(
                "public_register.html",
                store_number=store_number,
                requested_position=requested_position,
            )

        registration = PendingRegistrationRequest(
            full_name=full_name,
            username=username,
            email=email,
            phone=phone,
            requested_position=requested_position,
            store_number=store_number,
        )
        registration.password_hash = generate_password_hash(password)

        db.session.add(registration)
        db.session.commit()

        return render_template("public_register_success.html")

    return render_template(
        "public_register.html",
        store_number=store_number,
        requested_position="",
    )


@auth_bp.route("/users/registration-requests", methods=["GET"])
@login_required
@role_required("admin", "supervisor", "general_manager")
def registration_requests():
    if not current_user_can_review_registration_requests():
        flash("You do not have permission to review registration requests.", "error")
        return redirect(url_for("dashboard.home"))

    visible_stores = registration_visible_store_numbers()

    query = PendingRegistrationRequest.query

    if visible_stores is not None:
        if not visible_stores:
            registrations = []
        else:
            query = query.filter(PendingRegistrationRequest.store_number.in_(visible_stores))
            registrations = query.order_by(
                PendingRegistrationRequest.status.asc(),
                PendingRegistrationRequest.created_at.desc(),
            ).all()
    else:
        registrations = query.order_by(
            PendingRegistrationRequest.status.asc(),
            PendingRegistrationRequest.created_at.desc(),
        ).all()

    return render_template(
        "registration_requests.html",
        registrations=registrations,
        status_counts=registration_status_counts(registrations),
        allowed_roles=allowed_registration_approval_roles(),
    )


@auth_bp.route("/users/registration-requests/<int:registration_id>/approve", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "general_manager")
def approve_registration_request(registration_id):
    registration = PendingRegistrationRequest.query.get_or_404(registration_id)

    if not can_review_registration_request(registration):
        abort(403)

    if registration.status != "pending":
        flash("This request has already been reviewed.", "error")
        return redirect(url_for("auth.registration_requests"))

    final_role = request.form.get("final_role", "").strip()
    allowed_roles = allowed_registration_approval_roles()

    if final_role not in allowed_roles:
        abort(403)

    existing_user = User.query.filter_by(username=registration.username).first()
    if existing_user:
        flash("A user with that username already exists.", "error")
        return redirect(url_for("auth.registration_requests"))

    user = User(
        name=registration.full_name,
        username=registration.username,
        password_hash=registration.password_hash,
        role=final_role,
        position=registration.requested_position,
        store_number=registration.store_number if final_role in ["tm", "manager", "general_manager"] else None,
        email=registration.email,
        notification_email=registration.email,
        email_enabled=True,
        is_active=True,
    )

    db.session.add(user)
    db.session.flush()

    registration.status = "approved"
    registration.approved_role = final_role
    registration.created_user_id = user.id
    registration.reviewed_by_user_id = session.get("user_id")
    registration.reviewed_at = datetime.utcnow()
    registration.review_notes = request.form.get("review_notes", "").strip() or None

    db.session.commit()

    connect_result = sync_registration_user_to_bpi_connect(user, registration, final_role)

    if connect_result.get("success"):
        flash(
            f"Approved {registration.full_name} as {final_role.replace('_', ' ').title()} and sent BPI Connect invite.",
            "success",
        )
    elif connect_result.get("skipped"):
        flash(
            f"Approved {registration.full_name} as {final_role.replace('_', ' ').title()}. BPI Connect sync skipped because integration is not configured.",
            "warning",
        )
    else:
        flash(
            f"Approved {registration.full_name} as {final_role.replace('_', ' ').title()}, but BPI Connect sync failed: {connect_result.get('error')}",
            "warning",
        )

    return redirect(url_for("auth.registration_requests"))


@auth_bp.route("/users/registration-requests/<int:registration_id>/reject", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "general_manager")
def reject_registration_request(registration_id):
    registration = PendingRegistrationRequest.query.get_or_404(registration_id)

    if not can_review_registration_request(registration):
        abort(403)

    if registration.status != "pending":
        flash("This request has already been reviewed.", "error")
        return redirect(url_for("auth.registration_requests"))

    registration.status = "rejected"
    registration.reviewed_by_user_id = session.get("user_id")
    registration.reviewed_at = datetime.utcnow()
    registration.review_notes = request.form.get("review_notes", "").strip() or None

    db.session.commit()

    flash(f"Rejected request for {registration.full_name}.", "success")
    return redirect(url_for("auth.registration_requests"))

