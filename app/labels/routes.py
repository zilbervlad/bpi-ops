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

    setup_url = ""
    if store_number:
        setup_url = f"bpilabels://setup?store={quote(str(store_number))}"

    qr_download_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=240x240&data={quote(android_app_url, safe='')}"
    )

    qr_setup_url = ""
    if setup_url:
        qr_setup_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=240x240&data={quote(setup_url, safe='')}"
        )

    can_admin_labels = role in {"admin", "supervisor"}

    return render_template(
        "labels/index.html",
        role=role,
        store_number=store_number,
        android_app_url=android_app_url,
        setup_url=setup_url,
        qr_download_url=qr_download_url,
        qr_setup_url=qr_setup_url,
        can_admin_labels=can_admin_labels,
        generated_at=datetime.now(),
    )
