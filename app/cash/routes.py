from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.extensions import db
from app.models import CashLog
from app.auth.routes import login_required

cash_bp = Blueprint("cash", __name__, url_prefix="/cash")


@cash_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    user_role = session.get("user_role")
    store_number = session.get("user_store")

    if user_role != "manager":
        flash("Only managers can access Cash Control.", "error")
        return redirect(url_for("dashboard.home"))

    if not store_number:
        flash("No store assigned to this user.", "error")
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        shift_type = (request.form.get("shift_type") or "").strip()
        log_date_raw = (request.form.get("log_date") or "").strip()
        manager_name = (request.form.get("manager_name") or session.get("user_name") or "").strip()

        if not shift_type:
            flash("Shift type is required.", "error")
            return redirect(url_for("cash.index"))

        if not log_date_raw:
            flash("Log date is required.", "error")
            return redirect(url_for("cash.index"))

        try:
            from datetime import datetime
            log_date = datetime.strptime(log_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid log date.", "error")
            return redirect(url_for("cash.index"))

        try:
            back_till = float(request.form.get("back_till") or 0)
            front_till = float(request.form.get("front_till") or 0)
            driver_banks = float(request.form.get("driver_banks") or 0)
        except ValueError:
            flash("Cash amounts must be valid numbers.", "error")
            return redirect(url_for("cash.index"))

        total_cash = back_till + front_till + driver_banks

        log = CashLog(
            store_number=store_number,
            log_date=log_date,
            shift_type=shift_type,
            back_till=back_till,
            front_till=front_till,
            driver_banks=driver_banks,
            total_cash=total_cash,
            manager_name=manager_name,
        )

        db.session.add(log)
        db.session.commit()

        flash("Cash log submitted successfully.", "success")
        return redirect(url_for("cash.index"))

    logs = (
        CashLog.query.filter_by(store_number=store_number)
        .order_by(CashLog.log_date.desc(), CashLog.created_at.desc())
        .limit(10)
        .all()
    )

    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")

    return render_template(
        "cash.html",
        logs=logs,
        today_str=today_str,
        store_number=store_number,
        manager_name=session.get("user_name", ""),
    )