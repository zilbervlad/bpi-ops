from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from app.extensions import db
from app.models import CashLog, Store
from app.auth.routes import login_required

cash_bp = Blueprint("cash", __name__, url_prefix="/cash")

APP_TZ = ZoneInfo("America/New_York")
BUSINESS_DAY_CUTOFF = time(5, 0)


def now_et():
    return datetime.now(APP_TZ)


def get_business_date():
    current = now_et()
    if current.time() < BUSINESS_DAY_CUTOFF:
        return (current - timedelta(days=1)).date()
    return current.date()


def get_manager_store():
    user_role = session.get("user_role")
    store_number = session.get("user_store")

    if user_role != "manager":
        flash("Only managers can access Cash Control.", "error")
        return None

    if not store_number:
        flash("No store assigned to this user.", "error")
        return None

    return store_number


def is_log_date_editable(log_date):
    return log_date == get_business_date()


def can_capture_checklist_cash(store_number):
    role = (session.get("user_role") or "").strip().lower()

    if role == "manager":
        return str(session.get("user_store") or "") == str(store_number)

    store = Store.query.filter_by(
        store_number=store_number,
        is_active=True,
    ).first()

    if not store:
        return False

    if role == "admin":
        return True

    if role == "supervisor":
        return str(store.area_name or "") == str(session.get("user_area") or "")

    return False


@cash_bp.route("/checklist-log", methods=["POST"])
@login_required
def checklist_log():
    data = request.get_json(silent=True) or {}

    store_number = str(data.get("store_number") or "").strip()
    log_date_raw = str(data.get("log_date") or "").strip()
    shift_type = str(data.get("shift_type") or "").strip().lower()
    manager_name = str(
        data.get("manager_name")
        or session.get("user_name")
        or ""
    ).strip()

    if not store_number or not log_date_raw:
        return jsonify({"success": False, "error": "Missing store/date."}), 400

    if shift_type not in {"opening", "midshift"}:
        return jsonify({"success": False, "error": "Invalid cash shift."}), 400

    if not can_capture_checklist_cash(store_number):
        return jsonify({"success": False, "error": "You do not have access to this store."}), 403

    try:
        log_date = datetime.strptime(log_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "error": "Invalid cash date."}), 400

    if not is_log_date_editable(log_date):
        return jsonify({
            "success": False,
            "error": "Only the active business day can be updated.",
        }), 400

    try:
        cash_on_hand = float(data.get("cash_on_hand"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Enter a valid cash amount."}), 400

    if cash_on_hand < 0:
        return jsonify({"success": False, "error": "Cash on hand cannot be negative."}), 400

    amount_to_account_for = None
    cash_over_short = None

    if shift_type == "midshift":
        try:
            amount_to_account_for = float(data.get("amount_to_account_for"))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Enter a valid total to account for.",
            }), 400

        if amount_to_account_for < 0:
            return jsonify({
                "success": False,
                "error": "Total to account for cannot be negative.",
            }), 400

        cash_over_short = cash_on_hand - amount_to_account_for

    log = (
        CashLog.query
        .filter_by(
            store_number=store_number,
            log_date=log_date,
            shift_type=shift_type,
        )
        .order_by(CashLog.created_at.desc(), CashLog.id.desc())
        .first()
    )

    created = log is None

    if log is None:
        log = CashLog(
            store_number=store_number,
            log_date=log_date,
            shift_type=shift_type,
            total_cash=cash_on_hand,
            amount_to_account_for=amount_to_account_for,
            cash_over_short=cash_over_short,
            manager_name=manager_name,
        )
        db.session.add(log)
    else:
        # Checklist capture is an aggregate cash-on-hand count. Keep any
        # component breakdown already entered through Cash Control intact.
        log.total_cash = cash_on_hand
        if manager_name:
            log.manager_name = manager_name

        if shift_type == "midshift":
            log.amount_to_account_for = amount_to_account_for
            log.cash_over_short = cash_over_short

    db.session.commit()

    return jsonify({
        "success": True,
        "cash_log_id": log.id,
        "created": created,
        "store_number": store_number,
        "log_date": log_date.isoformat(),
        "shift_type": shift_type,
        "total_cash": log.total_cash,
        "amount_to_account_for": log.amount_to_account_for,
        "cash_over_short": log.cash_over_short,
    })


@cash_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    store_number = get_manager_store()
    if not store_number:
        return redirect(url_for("dashboard.home"))

    active_business_date = get_business_date()

    if request.method == "POST":
        edit_log_id = (request.form.get("edit_log_id") or "").strip()
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
            log_date = datetime.strptime(log_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid log date.", "error")
            return redirect(url_for("cash.index"))

        if not is_log_date_editable(log_date):
            flash("Only the active business day can be edited. Older cash logs are read-only.", "error")
            return redirect(url_for("cash.index"))

        try:
            back_till = float(request.form.get("back_till") or 0)
            front_till = float(request.form.get("front_till") or 0)
            driver_banks = float(request.form.get("driver_banks") or 0)
        except ValueError:
            flash("Cash amounts must be valid numbers.", "error")
            return redirect(url_for("cash.index"))

        total_cash = back_till + front_till + driver_banks

        amount_to_account_for = None
        cash_over_short = None

        if shift_type == "midshift":
            try:
                amount_to_account_for = float(request.form.get("amount_to_account_for") or 0)
            except ValueError:
                flash("Amount to account for must be a valid number.", "error")
                return redirect(url_for("cash.index"))

            cash_over_short = total_cash - amount_to_account_for

        if edit_log_id:
            log = CashLog.query.get(edit_log_id)

            if not log:
                flash("Cash log not found.", "error")
                return redirect(url_for("cash.index"))

            if log.store_number != store_number:
                flash("You can only edit cash logs for your own store.", "error")
                return redirect(url_for("cash.index"))

            if not is_log_date_editable(log.log_date):
                flash("That cash log is read-only because it is from a prior business day.", "error")
                return redirect(url_for("cash.index"))

            log.shift_type = shift_type
            log.log_date = log_date
            log.manager_name = manager_name
            log.back_till = back_till
            log.front_till = front_till
            log.driver_banks = driver_banks
            log.total_cash = total_cash
            log.amount_to_account_for = amount_to_account_for
            log.cash_over_short = cash_over_short

            db.session.commit()
            flash("Cash log updated successfully.", "success")
            return redirect(url_for("cash.index"))

        log = CashLog(
            store_number=store_number,
            log_date=log_date,
            shift_type=shift_type,
            back_till=back_till,
            front_till=front_till,
            driver_banks=driver_banks,
            total_cash=total_cash,
            amount_to_account_for=amount_to_account_for,
            cash_over_short=cash_over_short,
            manager_name=manager_name,
        )

        db.session.add(log)
        db.session.commit()

        flash("Cash log submitted successfully.", "success")
        return redirect(url_for("cash.index"))

    edit_id = request.args.get("edit")
    edit_log = None

    if edit_id:
        edit_log = CashLog.query.get(edit_id)
        if not edit_log:
            flash("Cash log not found.", "error")
            return redirect(url_for("cash.index"))
        if edit_log.store_number != store_number:
            flash("You can only view cash logs for your own store.", "error")
            return redirect(url_for("cash.index"))

    logs = (
        CashLog.query.filter_by(store_number=store_number)
        .order_by(CashLog.log_date.desc(), CashLog.created_at.desc())
        .limit(10)
        .all()
    )

    read_only = False
    if edit_log:
        read_only = not is_log_date_editable(edit_log.log_date)

    form_log_date = edit_log.log_date if edit_log else active_business_date

    return render_template(
        "cash.html",
        logs=logs,
        today_str=form_log_date.strftime("%Y-%m-%d"),
        active_business_date_str=active_business_date.strftime("%Y-%m-%d"),
        store_number=store_number,
        manager_name=edit_log.manager_name if edit_log and edit_log.manager_name else session.get("user_name", ""),
        edit_log=edit_log,
        read_only=read_only,
    )