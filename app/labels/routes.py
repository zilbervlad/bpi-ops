from datetime import datetime
from urllib.parse import quote

from flask import Blueprint, render_template, request, session

labels_bp = Blueprint("labels", __name__, url_prefix="/labels")


def _current_store_number():
    return (
        session.get("selected_store_number")
        or session.get("store_number")
        or session.get("current_store_number")
        or ""
    )


@labels_bp.route("/")
def index():
    role = (session.get("account_role") or session.get("role") or "").lower()
    store_number = request.args.get("store") or _current_store_number()

    # Keep this configurable so we can point it to the APK/install page later
    android_app_url = "https://bpi-labels.onrender.com"

    qr_download_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=240x240&data={quote(android_app_url, safe='')}"
    )

    # Store setup QR will come in Phase 2 after the Android app supports
    # a real setup/deep-link flow. For now, stores choose their store
    # inside the Android app like they do today.

    can_admin_labels = role in {"admin", "supervisor"}

    return render_template(
        "labels/index.html",
        role=role,
        store_number=store_number,
        android_app_url=android_app_url,
        qr_download_url=qr_download_url,
        can_admin_labels=can_admin_labels,
        generated_at=datetime.now(),
    )
