from functools import wraps

from flask import flash, redirect, request, session, url_for

from app.models import User


class CurrentUserProxy:
    """Expose the BPI Ops session user using the interface Academy expects."""

    @property
    def id(self):
        return session.get("user_id")

    @property
    def is_authenticated(self):
        return bool(session.get("user_id"))

    @property
    def is_anonymous(self):
        return not self.is_authenticated

    @property
    def user(self):
        user_id = session.get("user_id")
        if not user_id:
            return None
        return User.query.get(int(user_id))

    def __getattr__(self, name):
        user = self.user
        if user is None:
            if name == "role":
                return None
            if name == "position":
                return None
            return None
        return getattr(user, name)


current_user = CurrentUserProxy()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to access BPI Academy.", "warning")
            return redirect(
                url_for(
                    "auth.login",
                    next=request.full_path if request.query_string else request.path,
                )
            )
        return view(*args, **kwargs)

    return wrapped
