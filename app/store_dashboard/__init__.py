from flask import Blueprint

store_dashboard_bp = Blueprint(
    "store_dashboard",
    __name__,
    url_prefix="/store-dashboard"
)

from . import routes