"""
Compatibility module for legacy MIT STS imports.

Route modules are registered explicitly from app.academy.__init__.
BPI Ops user management remains the only user-management system.
"""

from .routes_shared import mit_sts_bp

__all__ = ["mit_sts_bp"]
