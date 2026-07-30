from .routes_shared import *


@mit_sts_bp.route("/promotion-queue")
@login_required
def promotion_queue():
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    mits = (
        MITProfile.query
        .join(User, MITProfile.user_id == User.id)
        .filter(User.is_active == True)
        .order_by(MITProfile.created_at.desc())
        .all()
    )

    ready_mits = []
    progress_map = {}

    for mit in mits:
        current_level = getattr(mit, "current_level", 1) or 1
        progress = calculate_level_progress(mit.id, current_level)
        progress_map[mit.id] = progress

        if progress >= 100:
            ready_mits.append(mit)

    return render_template(
        "academy/mit_sts/promotion_queue.html",
        queue=ready_mits,
        progress_map=progress_map,
        user=current_user,
    )


@mit_sts_bp.route("/promote/<int:mit_id>", methods=["POST"])
@login_required
def promote_mit(mit_id):
    if not is_coach():
        return redirect(url_for("mit_sts.dashboard"))

    mit = MITProfile.query.get_or_404(mit_id)

    current_level = getattr(mit, "current_level", 1) or 1
    current_progress = calculate_level_progress(mit.id, current_level)

    if current_progress < 100:
        flash("This MIT is not ready for promotion yet.", "danger")
        return redirect(url_for("mit_sts.promotion_queue"))

    if current_level >= 3:
        flash("This MIT is already at the highest tracked level.", "danger")
        return redirect(url_for("mit_sts.promotion_queue"))

    next_level = current_level + 1

    promotion = MITPromotion(
        mit_profile_id=mit.id,
        approved_by_user_id=current_user.id,
        effective_date=date.today(),
        from_level=current_level,
        to_level=str(next_level),
    )

    mit.current_level = next_level
    mit.target_level = get_target_level(next_level)
    mit.sts_status = "on_track"
    mit.next_review_date = None

    db.session.add(promotion)
    db.session.commit()

    flash(f"MIT promoted from Level {current_level} to Level {next_level}.", "success")
    return redirect(url_for("mit_sts.promotion_queue"))
