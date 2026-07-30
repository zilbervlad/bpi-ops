from .home import academy_bp
from .routes_shared import mit_sts_bp

# MIT STS route modules.
# BPI Ops remains the source of truth for users and authentication.
from . import dashboard_routes  # noqa: F401
from . import template_routes  # noqa: F401
from . import mit_profile_routes  # noqa: F401
from . import level_routes  # noqa: F401
from . import task_routes  # noqa: F401
from . import promotion_routes  # noqa: F401
from . import export_routes  # noqa: F401

__all__ = ["academy_bp", "mit_sts_bp"]
