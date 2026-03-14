from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import Store

store_admin_bp = Blueprint("store_admin", __name__, url_prefix="/store-admin")


@store_admin_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("admin")
def index():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "create":
            store_number = request.form.get("store_number", "").strip()
            store_name = request.form.get("store_name", "").strip()
            area_name = request.form.get("area_name", "").strip()

            if not store_number or not area_name:
                flash("Store number and area are required.", "error")
                return redirect(url_for("store_admin.index"))

            existing = Store.query.filter_by(store_number=store_number).first()
            if existing:
                flash("That store number already exists.", "error")
                return redirect(url_for("store_admin.index"))

            db.session.add(
                Store(
                    store_number=store_number,
                    store_name=store_name or f"Store {store_number}",
                    area_name=area_name,
                    is_active=True,
                )
            )
            db.session.commit()

            flash("Store created successfully.", "success")
            return redirect(url_for("store_admin.index"))

        if action == "update":
            store_id = request.form.get("store_id", "").strip()
            store = Store.query.get(store_id)

            if not store:
                flash("Store not found.", "error")
                return redirect(url_for("store_admin.index"))

            new_store_number = request.form.get("store_number", "").strip()
            new_store_name = request.form.get("store_name", "").strip()
            new_area_name = request.form.get("area_name", "").strip()

            if not new_store_number or not new_area_name:
                flash("Store number and area are required.", "error")
                return redirect(url_for("store_admin.index"))

            duplicate = Store.query.filter(
                Store.store_number == new_store_number,
                Store.id != store.id
            ).first()

            if duplicate:
                flash("Another store already uses that store number.", "error")
                return redirect(url_for("store_admin.index"))

            store.store_number = new_store_number
            store.store_name = new_store_name or f"Store {new_store_number}"
            store.area_name = new_area_name
            store.is_active = request.form.get("is_active") == "on"

            db.session.commit()
            flash("Store updated successfully.", "success")
            return redirect(url_for("store_admin.index"))

    stores = Store.query.order_by(Store.area_name.asc(), Store.store_number.asc()).all()

    existing_areas = sorted(
        {store.area_name for store in stores if store.area_name}
    )

    return render_template(
        "store_admin.html",
        stores=stores,
        existing_areas=existing_areas,
    )