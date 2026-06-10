from flask import Blueprint

dwp_bp = Blueprint("dwp", __name__, url_prefix="/dwp")

from app.dwp import routes  # noqa: E402,F401
