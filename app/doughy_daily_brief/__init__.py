from flask import Blueprint


doughy_daily_brief_bp = Blueprint(
    "doughy_daily_brief",
    __name__,
    url_prefix="/api/internal/doughy-daily-brief",
)


from app.doughy_daily_brief import routes  # noqa: E402,F401
