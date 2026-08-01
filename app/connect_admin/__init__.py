from .routes import connect_admin_bp
from .active_users_view import install as install_active_users_view

install_active_users_view(connect_admin_bp)
