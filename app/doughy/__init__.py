from flask import Blueprint

doughy_bp = Blueprint("doughy", __name__, url_prefix="/doughy")

from . import routes  # noqa: E402,F401
