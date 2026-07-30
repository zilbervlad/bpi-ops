from flask import jsonify
from .routes_shared import *

@mit_sts_bp.route("/templates")
@login_required
def template_library():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    templates = MITLevelTemplate.query.order_by(
        MITLevelTemplate.level_number.asc(),
        MITLevelTemplate.category.asc(),
        MITLevelTemplate.sort_order.asc(),
        MITLevelTemplate.id.asc(),
    ).all()

    grouped_templates = defaultdict(list)
    for item in templates:
        grouped_templates[item.level_number].append(item)

    return render_template(
        "academy/mit_sts/template_library.html",
        templates=templates,
        grouped_templates=dict(grouped_templates),
        user=current_user,
    )


@mit_sts_bp.route("/templates/new", methods=["GET", "POST"])
@login_required
def new_template_item():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    if request.method == "POST":
        level_number = request.form.get("level_number", "1")
        item_name = request.form.get("item_name", "").strip()

        if not item_name:
            flash("Item name is required.", "danger")
            return redirect(url_for("mit_sts.new_template_item"))

        item = MITLevelTemplate(
            level_number=int(level_number),
            item_name=item_name,
            category=request.form.get("category") or None,
            item_description=request.form.get("item_description") or None,
            sort_order=int(request.form.get("sort_order") or 0),
            source_ref=request.form.get("source_ref") or None,
            is_required=request.form.get("is_required") == "on",
        )
        db.session.add(item)
        db.session.commit()

        flash("Template created", "success")
        return redirect(url_for("mit_sts.template_library"))

    return render_template(
        "academy/mit_sts/template_form.html",
        page_title="Create STS Item",
        submit_label="Create STS Item",
        item=None,
        user=current_user,
    )


@mit_sts_bp.route("/templates/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_template_item(item_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    item = MITLevelTemplate.query.get_or_404(item_id)

    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()

        if not item_name:
            flash("Item name is required.", "danger")
            return redirect(url_for("mit_sts.edit_template_item", item_id=item.id))

        item.level_number = int(request.form.get("level_number") or item.level_number)
        item.item_name = item_name
        item.category = request.form.get("category") or None
        item.item_description = request.form.get("item_description") or None
        item.sort_order = int(request.form.get("sort_order") or 0)
        item.source_ref = request.form.get("source_ref") or None
        item.is_required = request.form.get("is_required") == "on"

        db.session.commit()

        flash("Template updated", "success")
        return redirect(url_for("mit_sts.template_library"))

    return render_template(
        "academy/mit_sts/template_form.html",
        page_title="Edit STS Item",
        submit_label="Save Changes",
        item=item,
        user=current_user,
    )


@mit_sts_bp.route("/templates/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_template_item(item_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    item = MITLevelTemplate.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()

    flash("Template deleted", "success")
    return redirect(url_for("mit_sts.template_library"))


@mit_sts_bp.route("/templates/reorder", methods=["POST"])
@login_required
def reorder_template_items():
    if not is_coach():
        return jsonify({
            "ok": False,
            "error": "You do not have permission to reorder STS items.",
        }), 403

    payload = request.get_json(silent=True) or {}

    try:
        level_number = int(payload.get("level_number"))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "A valid level number is required.",
        }), 400

    raw_item_ids = payload.get("item_ids")

    if not isinstance(raw_item_ids, list) or not raw_item_ids:
        return jsonify({
            "ok": False,
            "error": "No STS items were supplied.",
        }), 400

    try:
        item_ids = [int(item_id) for item_id in raw_item_ids]
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "Invalid STS item ID.",
        }), 400

    if len(item_ids) != len(set(item_ids)):
        return jsonify({
            "ok": False,
            "error": "Duplicate STS items were supplied.",
        }), 400

    items = MITLevelTemplate.query.filter(
        MITLevelTemplate.id.in_(item_ids)
    ).all()

    item_map = {item.id: item for item in items}

    if len(item_map) != len(item_ids):
        return jsonify({
            "ok": False,
            "error": "One or more STS items were not found.",
        }), 404

    wrong_level = [
        item.id
        for item in items
        if item.level_number != level_number
    ]

    if wrong_level:
        return jsonify({
            "ok": False,
            "error": "Items cannot be moved between levels by dragging.",
        }), 400

    for sort_order, item_id in enumerate(item_ids, start=1):
        item_map[item_id].sort_order = sort_order

    db.session.commit()

    return jsonify({
        "ok": True,
        "level_number": level_number,
        "saved_count": len(item_ids),
    })

