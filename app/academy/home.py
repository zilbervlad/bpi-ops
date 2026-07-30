from flask import Blueprint, redirect, render_template, session, url_for

from app.models import MITProfile


academy_bp = Blueprint("academy", __name__, url_prefix="/academy")


@academy_bp.route("/")
def index():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    role = session.get("account_role", session.get("user_role"))
    profile = MITProfile.query.filter_by(user_id=user_id).first()

    return render_template(
        "academy/index.html",
        mit_profile=profile,
        can_manage=role in {"admin", "supervisor"},
    )
