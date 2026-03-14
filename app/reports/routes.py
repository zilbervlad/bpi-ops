from flask import Blueprint, render_template
from app.auth.routes import login_required, role_required

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@reports_bp.route("/")
@login_required
@role_required("admin", "supervisor")
def index():
    return render_template("reports.html")