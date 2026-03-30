from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    VerificationTemplateField,
    VerificationReport,
    VerificationReportValue,
    Store
)
from app.services.email_service import send_email

verification_bp = Blueprint("verification", __name__, url_prefix="/verification")


def get_supervisor_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def ensure_default_template():
    defaults = [
        ("bad_orders", "Bad order / cancel log system in place?", "textarea"),
        ("suspicious_activity", "Anyone identified for suspicious activity / callbacks made?", "textarea"),
        ("csr_program", "Is CSR development program in use?", "textarea"),
        ("dumpster_check", "Check dumpsters for waste - what did you see?", "textarea"),
    ]

    existing = {f.field_key: f for f in VerificationTemplateField.query.all()}
    active_keys = {key for key, _, _ in defaults}

    for i, (key, label, ftype) in enumerate(defaults, start=1):
        if key in existing:
            field = existing[key]
            field.field_label = label
            field.field_type = ftype
            field.sort_order = i
            field.is_active = True
        else:
            db.session.add(
                VerificationTemplateField(
                    field_key=key,
                    field_label=label,
                    field_type=ftype,
                    sort_order=i,
                    is_active=True
                )
            )

    for field in existing.values():
        if field.field_key not in active_keys:
            field.is_active = field.is_active

    db.session.commit()


@verification_bp.route("/")
@login_required
@role_required("admin", "supervisor")
def index():
    return redirect(url_for("verification.new_report"))


@verification_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor")
def new_report():
    stores = get_supervisor_stores()
    ensure_default_template()

    if not stores:
        flash("No stores available for verification.", "error")
        return redirect(url_for("dashboard.home"))

    fields = VerificationTemplateField.query.filter_by(is_active=True).order_by(
        VerificationTemplateField.sort_order.asc(),
        VerificationTemplateField.id.asc()
    ).all()

    allowed_store_numbers = {store.store_number for store in stores}

    if request.method == "POST":
        store_number = (request.form.get("store_number") or "").strip()

        if store_number not in allowed_store_numbers:
            flash("Invalid store selection.", "error")
            return redirect(url_for("verification.new_report"))

        report = VerificationReport(
            store_number=store_number,
            supervisor_name=session.get("user_name"),
            created_by_user_id=session.get("user_id"),
        )
        db.session.add(report)
        db.session.flush()

        for field in fields:
            value = (request.form.get(field.field_key) or "").strip()

            db.session.add(
                VerificationReportValue(
                    report_id=report.id,
                    template_field_id=field.id,
                    field_key=field.field_key,
                    field_label=field.field_label,
                    sort_order=field.sort_order,
                    value_text=value,
                )
            )

        db.session.commit()

        try:
            body = f"Verification Report - Store {store_number}\n\n"
            body += f"Submitted by: {session.get('user_name') or 'Unknown'}\n"
            body += f"Submitted at: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n\n"

            for field in fields:
                val = (request.form.get(field.field_key) or "").strip()
                body += f"{field.field_label}:\n{val or '—'}\n\n"

            supervisor_email = session.get("user_email") or session.get("notification_email")
            send_email(
                to_email="YOUR_EMAIL@gmail.com",
                subject=f"Verification - Store {store_number}",
                body=body,
                cc_emails=supervisor_email if supervisor_email else None
            )
        except Exception as e:
            print("Email failed:", e)

        flash("Verification submitted.", "success")
        return redirect(url_for("dashboard.home"))

    return render_template(
        "verification_form.html",
        stores=stores,
        fields=fields
    )


@verification_bp.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin():
    ensure_default_template()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "create":
            field_key = (request.form.get("field_key") or "").strip()
            field_label = (request.form.get("field_label") or "").strip()
            field_type = (request.form.get("field_type") or "textarea").strip()
            sort_order_raw = (request.form.get("sort_order") or "999").strip()

            if not field_key or not field_label:
                flash("Field key and label are required.", "error")
                return redirect(url_for("verification.admin"))

            try:
                sort_order = int(sort_order_raw)
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("verification.admin"))

            existing = VerificationTemplateField.query.filter_by(field_key=field_key).first()
            if existing:
                flash("That field key already exists.", "error")
                return redirect(url_for("verification.admin"))

            db.session.add(
                VerificationTemplateField(
                    field_key=field_key,
                    field_label=field_label,
                    field_type=field_type,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            db.session.commit()
            flash("Verification field created.", "success")
            return redirect(url_for("verification.admin"))

        if action == "update":
            field_id = (request.form.get("field_id") or "").strip()
            field = VerificationTemplateField.query.get(field_id)

            if not field:
                flash("Field not found.", "error")
                return redirect(url_for("verification.admin"))

            field.field_key = (request.form.get("field_key") or "").strip()
            field.field_label = (request.form.get("field_label") or "").strip()
            field.field_type = (request.form.get("field_type") or "textarea").strip()

            try:
                field.sort_order = int((request.form.get("sort_order") or "999").strip())
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("verification.admin"))

            field.is_active = request.form.get("is_active") == "on"

            duplicate = VerificationTemplateField.query.filter(
                VerificationTemplateField.field_key == field.field_key,
                VerificationTemplateField.id != field.id
            ).first()
            if duplicate:
                flash("That field key already exists.", "error")
                return redirect(url_for("verification.admin"))

            if not field.field_key or not field.field_label:
                flash("Field key and label are required.", "error")
                return redirect(url_for("verification.admin"))

            db.session.commit()
            flash("Verification field updated.", "success")
            return redirect(url_for("verification.admin"))

    fields = VerificationTemplateField.query.order_by(
        VerificationTemplateField.sort_order.asc(),
        VerificationTemplateField.id.asc()
    ).all()

    return render_template("verification_admin.html", fields=fields)