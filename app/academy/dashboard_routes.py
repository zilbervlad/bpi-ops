from .routes_shared import *


@mit_sts_bp.route("/")
@mit_sts_bp.route("/dashboard")
@login_required
def dashboard():
    if not is_coach():
        return redirect(url_for("mit_sts.my_mit"))

    mits = (
        MITProfile.query
        .join(User, MITProfile.user_id == User.id)
        .filter(User.is_active == True)
        .order_by(MITProfile.created_at.desc())
        .all()
    )

    recent_progress_map = {}

    level_1_count = 0
    level_2_count = 0
    level_3_count = 0

    total_level_sum = 0
    total_mit_count = 0

    coach_scores = defaultdict(lambda: {
        "coach_name": "Unassigned",
        "total_level": 0,
        "mit_count": 0,
        "score": 0,
    })

    for mit in mits:
        current_level = getattr(mit, "current_level", 1) or 1
        progress = calculate_level_progress(mit.id, current_level)
        recent_progress_map[mit.id] = progress

        total_level_sum += current_level
        total_mit_count += 1

        if current_level == 1:
            level_1_count += 1
        elif current_level == 2:
            level_2_count += 1
        elif current_level == 3:
            level_3_count += 1

        coach_user = getattr(mit, "coach_user", None)
        coach_name = coach_user.name if coach_user else "Unassigned"

        coach_scores[coach_name]["coach_name"] = coach_name
        coach_scores[coach_name]["total_level"] += current_level
        coach_scores[coach_name]["mit_count"] += 1

    company_score = round(total_level_sum / total_mit_count, 1) if total_mit_count else 0

    for coach in coach_scores.values():
        if coach["mit_count"]:
            coach["score"] = round(coach["total_level"] / coach["mit_count"], 1)

    recent_mits = mits[:5]

    return render_template(
        "academy/mit_sts/dashboard.html",
        mits=mits,
        recent_mits=recent_mits,
        total_mits=len(mits),
        level_1_count=level_1_count,
        level_2_count=level_2_count,
        level_3_count=level_3_count,
        company_score=company_score,
        coach_scores=dict(coach_scores),
        recent_progress_map=recent_progress_map,
        user=current_user,
    )
