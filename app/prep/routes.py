from collections import defaultdict
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import PrepTemplateItem, DailyPrep, DailyPrepItem, Store, today_et

prep_bp = Blueprint("prep", __name__, url_prefix="/prep")


SECTION_OPTIONS = [
    "Sauces",
    "Cheese",
    "Chicken / Wings / Boneless",
    "Veggie / Specialty / Other",
]


def weekday_field_name(prep_date):
    return prep_date.strftime("%A").lower()


def get_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")
    user_store = session.get("user_store")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    if role == "manager":
        return Store.query.filter_by(
            store_number=user_store,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def get_allowed_store_numbers():
    return {store.store_number for store in get_visible_stores()}


def sync_missing_daily_prep_items(daily):
    weekday_name = weekday_field_name(daily.prep_date)

    template_items = PrepTemplateItem.query.filter_by(
        store_number=daily.store_number,
        is_active=True
    ).order_by(
        PrepTemplateItem.section_name.asc(),
        PrepTemplateItem.sort_order.asc(),
        PrepTemplateItem.id.asc()
    ).all()

    existing_template_ids = {
        item.template_item_id
        for item in daily.items
        if item.template_item_id is not None
    }

    added_any = False

    for template in template_items:
        if not getattr(template, weekday_name, False):
            continue

        if template.id in existing_template_ids:
            continue

        db.session.add(
            DailyPrepItem(
                daily_prep_id=daily.id,
                template_item_id=template.id,
                section_name=template.section_name,
                item_name=template.item_name,
                build_to=template.build_to,
                instructions=template.instructions,
                is_completed=False,
                completed_at=None,
            )
        )
        added_any = True

    if added_any:
        db.session.commit()

    return added_any


def get_or_create_daily_prep(store_number, prep_date):
    daily = DailyPrep.query.filter_by(
        store_number=store_number,
        prep_date=prep_date
    ).first()

    if not daily:
        daily = DailyPrep(
            store_number=store_number,
            prep_date=prep_date,
        )
        db.session.add(daily)
        db.session.flush()

        weekday_name = weekday_field_name(prep_date)

        template_items = PrepTemplateItem.query.filter_by(
            store_number=store_number,
            is_active=True
        ).order_by(
            PrepTemplateItem.section_name.asc(),
            PrepTemplateItem.sort_order.asc(),
            PrepTemplateItem.id.asc()
        ).all()

        for template in template_items:
            if not getattr(template, weekday_name, False):
                continue

            db.session.add(
                DailyPrepItem(
                    daily_prep_id=daily.id,
                    template_item_id=template.id,
                    section_name=template.section_name,
                    item_name=template.item_name,
                    build_to=template.build_to,
                    instructions=template.instructions,
                    is_completed=False,
                    completed_at=None,
                )
            )

        db.session.commit()
        return daily

    sync_missing_daily_prep_items(daily)
    return daily


def build_grouped_items(items):
    grouped = defaultdict(list)

    for item in items:
        grouped[item.section_name].append(item)

    ordered = {}
    for section in SECTION_OPTIONS:
        ordered[section] = grouped.get(section, [])

    for section_name in sorted(grouped.keys()):
        if section_name not in ordered:
            ordered[section_name] = grouped[section_name]

    return ordered


@prep_bp.route("/", methods=["GET"])
@login_required
@role_required("admin", "supervisor", "manager")
def index():
    visible_stores = get_visible_stores()

    if not visible_stores:
        flash("No stores are assigned to this user.", "error")
        return render_template(
            "placeholder.html",
            page_title="Prep Module",
            page_message="No stores are assigned to this user."
        )

    default_store = visible_stores[0].store_number
    requested_store = request.args.get("store", default_store).strip()
    allowed_store_numbers = {store.store_number for store in visible_stores}

    store_number = requested_store if requested_store in allowed_store_numbers else default_store

    requested_date_str = request.args.get("date", "").strip()
    today = today_et()

    if requested_date_str:
        try:
            selected_date = datetime.strptime(requested_date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    is_read_only = selected_date < today

    daily = get_or_create_daily_prep(store_number, selected_date)
    grouped_items = build_grouped_items(
        sorted(daily.items, key=lambda x: (x.section_name, x.item_name, x.id))
    )

    total_items = len(daily.items)
    completed_items = sum(1 for item in daily.items if item.is_completed)

    history = DailyPrep.query.filter_by(
        store_number=store_number
    ).order_by(DailyPrep.prep_date.desc()).limit(14).all()

    return render_template(
        "prep/index.html",
        daily=daily,
        grouped_items=grouped_items,
        store_number=store_number,
        selected_date=selected_date.strftime("%Y-%m-%d"),
        today_label=selected_date.strftime("%B %d, %Y"),
        stores=visible_stores,
        history=history,
        total_items=total_items,
        completed_items=completed_items,
        is_read_only=is_read_only,
    )


@prep_bp.route("/autosave-item", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "manager")
def autosave_item():
    data = request.get_json() or {}

    item_id = data.get("item_id")
    is_completed = bool(data.get("is_completed", False))

    item = DailyPrepItem.query.get(item_id)
    if not item:
        return jsonify({"success": False, "error": "Prep item not found."}), 404

    allowed_store_numbers = get_allowed_store_numbers()
    if item.daily_prep.store_number not in allowed_store_numbers:
        return jsonify({"success": False, "error": "Unauthorized."}), 403

    if item.daily_prep.prep_date < today_et():
        return jsonify({"success": False, "error": "Past prep sheets are read-only."}), 400

    item.is_completed = is_completed
    item.completed_at = datetime.utcnow() if is_completed else None
    db.session.commit()

    daily = item.daily_prep
    total_items = len(daily.items)
    completed_items = sum(1 for row in daily.items if row.is_completed)

    return jsonify({
        "success": True,
        "completed_items": completed_items,
        "total_items": total_items,
    })


@prep_bp.route("/manage-autosave", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def manage_autosave():
    data = request.get_json() or {}

    item_id = data.get("item_id")
    store_number = (data.get("store_number") or "").strip()

    if not item_id or not store_number:
        return jsonify({"success": False, "error": "Missing item/store."}), 400

    allowed_store_numbers = get_allowed_store_numbers()
    if store_number not in allowed_store_numbers:
        return jsonify({"success": False, "error": "Unauthorized."}), 403

    item = PrepTemplateItem.query.get(item_id)
    if not item or item.store_number != store_number:
        return jsonify({"success": False, "error": "Prep item not found."}), 404

    section_name = (data.get("section_name") or "").strip()
    item_name = (data.get("item_name") or "").strip()
    build_to = (data.get("build_to") or "").strip()
    instructions = (data.get("instructions") or "").strip()

    if not section_name or not item_name:
        return jsonify({"success": False, "error": "Section and item name are required."}), 400

    try:
        sort_order = int(str(data.get("sort_order", "0")).strip() or "0")
    except ValueError:
        return jsonify({"success": False, "error": "Sort order must be a number."}), 400

    item.section_name = section_name
    item.item_name = item_name
    item.build_to = build_to or None
    item.instructions = instructions or None
    item.sort_order = sort_order

    item.monday = bool(data.get("monday", False))
    item.tuesday = bool(data.get("tuesday", False))
    item.wednesday = bool(data.get("wednesday", False))
    item.thursday = bool(data.get("thursday", False))
    item.friday = bool(data.get("friday", False))
    item.saturday = bool(data.get("saturday", False))
    item.sunday = bool(data.get("sunday", False))
    item.is_active = bool(data.get("is_active", False))

    db.session.commit()

    return jsonify({"success": True})


@prep_bp.route("/manage", methods=["GET", "POST"])
@login_required
@role_required("admin", "supervisor")
def manage():
    visible_stores = get_visible_stores()

    if not visible_stores:
        flash("No stores are assigned to this user.", "error")
        return render_template(
            "placeholder.html",
            page_title="Prep Admin",
            page_message="No stores are assigned to this user."
        )

    default_store = visible_stores[0].store_number
    requested_store = request.args.get("store", default_store).strip()
    allowed_store_numbers = {store.store_number for store in visible_stores}

    store_number = requested_store if requested_store in allowed_store_numbers else default_store
    show_inactive = request.args.get("show_inactive", "").strip().lower() in {"1", "true", "yes", "on"}

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "create":
            section_name = request.form.get("section_name", "").strip()
            item_name = request.form.get("item_name", "").strip()
            build_to = request.form.get("build_to", "").strip()
            instructions = request.form.get("instructions", "").strip()
            sort_order_raw = request.form.get("sort_order", "0").strip()

            if not section_name or not item_name:
                flash("Section and item name are required.", "error")
                return redirect(url_for("prep.manage", store=store_number, show_inactive=int(show_inactive)))

            try:
                sort_order = int(sort_order_raw or "0")
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("prep.manage", store=store_number, show_inactive=int(show_inactive)))

            item = PrepTemplateItem(
                store_number=store_number,
                section_name=section_name,
                item_name=item_name,
                build_to=build_to or None,
                instructions=instructions or None,
                monday=request.form.get("monday") == "on",
                tuesday=request.form.get("tuesday") == "on",
                wednesday=request.form.get("wednesday") == "on",
                thursday=request.form.get("thursday") == "on",
                friday=request.form.get("friday") == "on",
                saturday=request.form.get("saturday") == "on",
                sunday=request.form.get("sunday") == "on",
                sort_order=sort_order,
                is_active=request.form.get("is_active") == "on",
            )
            db.session.add(item)
            db.session.commit()
            flash("Prep item created.", "success")
            return redirect(url_for("prep.manage", store=store_number, show_inactive=int(show_inactive)))

        if action == "delete":
            item_id = request.form.get("item_id", "").strip()
            item = PrepTemplateItem.query.get(item_id)

            if not item or item.store_number != store_number:
                flash("Prep item not found.", "error")
                return redirect(url_for("prep.manage", store=store_number, show_inactive=int(show_inactive)))

            usage_exists = DailyPrepItem.query.filter_by(template_item_id=item.id).first()

            if usage_exists:
                item.is_active = False
                db.session.commit()
                flash("Prep item archived because it already exists in prep history.", "success")
            else:
                db.session.delete(item)
                db.session.commit()
                flash("Prep item deleted.", "success")

            return redirect(url_for("prep.manage", store=store_number, show_inactive=int(show_inactive)))

    items_query = PrepTemplateItem.query.filter_by(
        store_number=store_number
    )

    if not show_inactive:
        items_query = items_query.filter_by(is_active=True)

    items = items_query.order_by(
        PrepTemplateItem.section_name.asc(),
        PrepTemplateItem.sort_order.asc(),
        PrepTemplateItem.id.asc()
    ).all()

    grouped_items = build_grouped_items(items)
    inactive_count = PrepTemplateItem.query.filter_by(store_number=store_number, is_active=False).count()

    return render_template(
        "prep/manage.html",
        items=items,
        grouped_items=grouped_items,
        stores=visible_stores,
        store_number=store_number,
        section_options=SECTION_OPTIONS,
        show_inactive=show_inactive,
        inactive_count=inactive_count,
    )