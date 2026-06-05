import csv
from datetime import datetime
from collections import Counter

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, abort, Response
from io import BytesIO, StringIO
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import User, Store, HRDocument, HRDocumentRecipient
from app.auth.routes import login_required, role_required
from app.services.email_service import send_email


hr_documents_bp = Blueprint("hr_documents", __name__, url_prefix="/hr-documents")

MAX_HR_DOCUMENT_BYTES = 10 * 1024 * 1024

ALLOWED_DOCUMENT_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "png",
    "jpg",
    "jpeg",
    "txt",
}


def current_user_id():
    return session.get("user_id")


def current_account_role():
    return session.get("account_role") or session.get("user_role")


def can_manage_hr_documents():
    return current_account_role() in {"admin", "hr"}


def allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_DOCUMENT_EXTENSIONS


def get_user_or_404():
    user_id = current_user_id()
    user = User.query.get(user_id)
    if not user:
        abort(403)
    return user


def user_can_access_document(document):
    if can_manage_hr_documents():
        return True

    user_id = current_user_id()
    if not user_id:
        return False

    return HRDocumentRecipient.query.filter_by(
        document_id=document.id,
        user_id=user_id,
    ).first() is not None


def recipient_query_for_target(target_mode, form):
    query = User.query.filter_by(is_active=True)

    if target_mode == "all":
        return query

    if target_mode == "role":
        role = form.get("target_role", "").strip()
        if not role:
            return None
        return query.filter(User.role == role)

    if target_mode == "position":
        position = form.get("target_position", "").strip()
        if not position:
            return None
        return query.filter(User.position == position)

    if target_mode == "store":
        store_number = form.get("target_store", "").strip()
        if not store_number:
            return None
        return query.filter(User.store_number == store_number)

    if target_mode == "individual":
        user_ids = [
            int(value)
            for value in form.getlist("target_user_ids")
            if value.isdigit()
        ]
        if not user_ids:
            return None
        return query.filter(User.id.in_(user_ids))

    return None



def add_recipients_to_document(document, selected_users):
    existing_user_ids = {
        row.user_id
        for row in HRDocumentRecipient.query.filter_by(document_id=document.id).all()
    }

    added_recipients = []
    skipped_count = 0

    for user in selected_users:
        if user.id in existing_user_ids:
            skipped_count += 1
            continue

        recipient = HRDocumentRecipient(
            document_id=document.id,
            user_id=user.id,
            status="pending",
        )
        db.session.add(recipient)
        added_recipients.append(recipient)

    db.session.flush()

    sent_count = 0
    failed_count = 0

    for recipient in added_recipients:
        if send_hr_document_email(document, recipient):
            sent_count += 1
        else:
            failed_count += 1

    return added_recipients, skipped_count, sent_count, failed_count


def send_hr_document_email(document, recipient):
    to_email = recipient.user.get_notification_email()
    if not to_email:
        recipient.email_error = "No notification email configured."
        return False

    document_url = url_for("hr_documents.acknowledge_document", document_id=document.id, _external=True)

    body = f"""Hello {recipient.user.name},

You have a new BPI Ops document to review and acknowledge.

Document: {document.title}

Open it here:
{document_url}

Please log in and complete the acknowledgment.

Boston Pie, Inc.
"""

    try:
        send_email(
            to_email=to_email,
            subject=f"BPI Ops Document Acknowledgment Required: {document.title}",
            body=body,
        )
        recipient.email_sent_at = datetime.utcnow()
        recipient.email_error = None
        return True
    except Exception as exc:
        recipient.email_error = str(exc)
        return False


@hr_documents_bp.route("/")
@login_required
@role_required("admin", "hr")
def index():
    documents = HRDocument.query.order_by(HRDocument.created_at.desc()).all()

    document_cards = []
    for document in documents:
        counts = Counter(recipient.status for recipient in document.recipients)
        total = len(document.recipients)
        acknowledged = counts.get("acknowledged", 0)

        document_cards.append({
            "document": document,
            "total": total,
            "acknowledged": acknowledged,
            "pending": total - acknowledged,
        })

    return render_template("hr_documents/index.html", document_cards=document_cards)


@hr_documents_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "hr")
def new_document():
    users = User.query.filter_by(is_active=True).order_by(User.name.asc()).all()
    stores = Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    roles = [
        ("admin", "Admin"),
        ("supervisor", "Supervisor"),
        ("general_manager", "General Manager"),
        ("manager", "Manager / Shift Runner"),
        ("tm", "TM"),
        ("maintenance", "Maintenance"),
        ("hr", "HR"),
    ]

    positions = [
        "CSR",
        "Driver",
        "MIT / Shift Runner",
        "Manager",
        "General Manager",
        "Supervisor",
        "Maintenance",
        "HR",
    ]

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        target_mode = request.form.get("target_mode", "").strip()
        upload = request.files.get("document_file")

        if not title:
            flash("Please enter a document title.", "error")
            return redirect(url_for("hr_documents.new_document"))

        if not upload or not upload.filename:
            flash("Please upload a document.", "error")
            return redirect(url_for("hr_documents.new_document"))

        filename = secure_filename(upload.filename)
        if not allowed_file(filename):
            flash("Allowed files: PDF, Word, image, or text documents.", "error")
            return redirect(url_for("hr_documents.new_document"))

        file_data = upload.read()
        if not file_data:
            flash("Uploaded file was empty.", "error")
            return redirect(url_for("hr_documents.new_document"))

        if len(file_data) > MAX_HR_DOCUMENT_BYTES:
            flash("File is too large. Maximum size is 10 MB.", "error")
            return redirect(url_for("hr_documents.new_document"))

        recipient_query = recipient_query_for_target(target_mode, request.form)
        if recipient_query is None:
            flash("Please choose valid recipients.", "error")
            return redirect(url_for("hr_documents.new_document"))

        selected_users = recipient_query.order_by(User.name.asc()).all()
        if not selected_users:
            flash("No active users matched that recipient selection.", "error")
            return redirect(url_for("hr_documents.new_document"))

        document = HRDocument(
            title=title,
            description=description,
            original_filename=filename,
            content_type=upload.mimetype,
            file_size=len(file_data),
            file_data=file_data,
            created_by_user_id=current_user_id(),
            is_active=True,
        )

        db.session.add(document)
        db.session.flush()

        recipients, skipped_count, sent_count, failed_count = add_recipients_to_document(document, selected_users)

        db.session.commit()

        flash(
            f"Document assigned to {len(recipients)} user(s). Emails sent: {sent_count}. Failed: {failed_count}. Skipped existing: {skipped_count}.",
            "success",
        )
        return redirect(url_for("hr_documents.detail", document_id=document.id))

    return render_template(
        "hr_documents/new.html",
        users=users,
        stores=stores,
        roles=roles,
        positions=positions,
        max_mb=MAX_HR_DOCUMENT_BYTES // (1024 * 1024),
    )


@hr_documents_bp.route("/my")
@login_required
def my_documents():
    user = get_user_or_404()

    recipients = HRDocumentRecipient.query.join(HRDocument).filter(
        HRDocumentRecipient.user_id == user.id,
        HRDocument.is_active == True,
    ).order_by(
        HRDocumentRecipient.status.asc(),
        HRDocumentRecipient.assigned_at.desc(),
    ).all()

    return render_template("hr_documents/my.html", recipients=recipients)


@hr_documents_bp.route("/<int:document_id>")
@login_required
@role_required("admin", "hr")
def detail(document_id):
    document = HRDocument.query.get_or_404(document_id)

    status_filter = request.args.get("status", "all").strip()
    store_filter = request.args.get("store", "all").strip()

    base_query = HRDocumentRecipient.query.filter_by(
        document_id=document.id,
    ).join(User)

    all_recipients = base_query.order_by(
        User.store_number.asc(),
        User.name.asc(),
    ).all()

    query = HRDocumentRecipient.query.filter_by(
        document_id=document.id,
    ).join(User)

    if status_filter == "pending":
        query = query.filter(HRDocumentRecipient.status != "acknowledged")
    elif status_filter == "acknowledged":
        query = query.filter(HRDocumentRecipient.status == "acknowledged")
    elif status_filter == "email_failed":
        query = query.filter(HRDocumentRecipient.email_error.isnot(None))

    if store_filter != "all":
        query = query.filter(User.store_number == store_filter)

    recipients = query.order_by(
        HRDocumentRecipient.status.asc(),
        User.store_number.asc(),
        User.name.asc(),
    ).all()

    store_options = sorted({
        recipient.user.store_number
        for recipient in all_recipients
        if recipient.user and recipient.user.store_number
    })

    total_count = len(all_recipients)
    acknowledged_count = sum(1 for recipient in all_recipients if recipient.status == "acknowledged")
    pending_count = total_count - acknowledged_count
    email_failed_count = sum(1 for recipient in all_recipients if recipient.email_error)

    return render_template(
        "hr_documents/detail.html",
        document=document,
        recipients=recipients,
        store_options=store_options,
        status_filter=status_filter,
        store_filter=store_filter,
        total_count=total_count,
        acknowledged_count=acknowledged_count,
        pending_count=pending_count,
        email_failed_count=email_failed_count,
    )




@hr_documents_bp.route("/<int:document_id>/add-recipients", methods=["GET", "POST"])
@login_required
@role_required("admin", "hr")
def add_recipients(document_id):
    document = HRDocument.query.get_or_404(document_id)

    users = User.query.filter_by(is_active=True).order_by(User.name.asc()).all()
    stores = Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    roles = [
        ("admin", "Admin"),
        ("supervisor", "Supervisor"),
        ("general_manager", "General Manager"),
        ("manager", "Manager / Shift Runner"),
        ("tm", "TM"),
        ("maintenance", "Maintenance"),
        ("hr", "HR"),
    ]

    positions = [
        "CSR",
        "Driver",
        "MIT / Shift Runner",
        "Manager",
        "General Manager",
        "Supervisor",
        "Maintenance",
        "HR",
    ]

    existing_user_ids = {
        row.user_id
        for row in HRDocumentRecipient.query.filter_by(document_id=document.id).all()
    }

    if request.method == "POST":
        target_mode = request.form.get("target_mode", "").strip()

        recipient_query = recipient_query_for_target(target_mode, request.form)
        if recipient_query is None:
            flash("Please choose valid recipients.", "error")
            return redirect(url_for("hr_documents.add_recipients", document_id=document.id))

        selected_users = recipient_query.order_by(User.name.asc()).all()
        if not selected_users:
            flash("No active users matched that recipient selection.", "error")
            return redirect(url_for("hr_documents.add_recipients", document_id=document.id))

        recipients, skipped_count, sent_count, failed_count = add_recipients_to_document(document, selected_users)

        db.session.commit()

        flash(
            f"Added {len(recipients)} new recipient(s). Emails sent: {sent_count}. Failed: {failed_count}. Skipped existing: {skipped_count}.",
            "success",
        )
        return redirect(url_for("hr_documents.detail", document_id=document.id))

    return render_template(
        "hr_documents/add_recipients.html",
        document=document,
        users=users,
        stores=stores,
        roles=roles,
        positions=positions,
        existing_user_ids=existing_user_ids,
    )


@hr_documents_bp.route("/<int:document_id>/resend/<int:recipient_id>", methods=["POST"])
@login_required
@role_required("admin", "hr")
def resend_document_email(document_id, recipient_id):
    document = HRDocument.query.get_or_404(document_id)
    recipient = HRDocumentRecipient.query.get_or_404(recipient_id)

    if recipient.document_id != document.id:
        abort(404)

    if recipient.status == "acknowledged":
        flash("This user already acknowledged the document.", "error")
        return redirect(url_for("hr_documents.detail", document_id=document.id))

    if send_hr_document_email(document, recipient):
        flash(f"Email resent to {recipient.user.name}.", "success")
    else:
        flash(f"Email failed for {recipient.user.name}: {recipient.email_error}", "error")

    db.session.commit()
    return redirect(url_for("hr_documents.detail", document_id=document.id))


@hr_documents_bp.route("/<int:document_id>/resend-pending", methods=["POST"])
@login_required
@role_required("admin", "hr")
def resend_pending_document_emails(document_id):
    document = HRDocument.query.get_or_404(document_id)

    recipients = HRDocumentRecipient.query.filter(
        HRDocumentRecipient.document_id == document.id,
        HRDocumentRecipient.status != "acknowledged",
    ).all()

    sent_count = 0
    failed_count = 0

    for recipient in recipients:
        if send_hr_document_email(document, recipient):
            sent_count += 1
        else:
            failed_count += 1

    db.session.commit()

    flash(f"Resent pending notifications. Sent: {sent_count}. Failed: {failed_count}.", "success")
    return redirect(url_for("hr_documents.detail", document_id=document.id))


@hr_documents_bp.route("/<int:document_id>/export")
@login_required
@role_required("admin", "hr")
def export_document_tracking(document_id):
    document = HRDocument.query.get_or_404(document_id)

    recipients = HRDocumentRecipient.query.filter_by(
        document_id=document.id,
    ).join(User).order_by(
        User.store_number.asc(),
        User.name.asc(),
    ).all()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Document",
        "Name",
        "Username",
        "Role",
        "Position",
        "Store",
        "Email",
        "Status",
        "Assigned At",
        "Email Sent At",
        "Email Error",
        "Acknowledged At",
        "Acknowledged Name",
    ])

    for recipient in recipients:
        user = recipient.user
        writer.writerow([
            document.title,
            user.name,
            user.username,
            user.role,
            getattr(user, "position", None) or "",
            user.store_number or "",
            user.get_notification_email() or "",
            recipient.status,
            recipient.assigned_at.strftime("%Y-%m-%d %H:%M:%S") if recipient.assigned_at else "",
            recipient.email_sent_at.strftime("%Y-%m-%d %H:%M:%S") if recipient.email_sent_at else "",
            recipient.email_error or "",
            recipient.acknowledged_at.strftime("%Y-%m-%d %H:%M:%S") if recipient.acknowledged_at else "",
            recipient.acknowledged_name or "",
        ])

    filename = f"hr_document_{document.id}_tracking.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@hr_documents_bp.route("/<int:document_id>/download")
@login_required
def download_document(document_id):
    document = HRDocument.query.get_or_404(document_id)

    if not user_can_access_document(document):
        abort(403)

    return send_file(
        BytesIO(document.file_data),
        mimetype=document.content_type or "application/octet-stream",
        as_attachment=True,
        download_name=document.original_filename,
    )


@hr_documents_bp.route("/<int:document_id>/acknowledge", methods=["GET", "POST"])
@login_required
def acknowledge_document(document_id):
    document = HRDocument.query.get_or_404(document_id)
    user = get_user_or_404()

    recipient = HRDocumentRecipient.query.filter_by(
        document_id=document.id,
        user_id=user.id,
    ).first()

    if not recipient and not can_manage_hr_documents():
        abort(403)

    if request.method == "POST":
        if not recipient:
            flash("This document is not assigned to your account.", "error")
            return redirect(url_for("hr_documents.my_documents"))

        acknowledged_name = request.form.get("acknowledged_name", "").strip()
        confirmed = request.form.get("confirmed") == "on"

        if not acknowledged_name or not confirmed:
            flash("Please type your name and check the acknowledgment box.", "error")
            return redirect(url_for("hr_documents.acknowledge_document", document_id=document.id))

        recipient.status = "acknowledged"
        recipient.acknowledged_at = datetime.utcnow()
        recipient.acknowledged_name = acknowledged_name
        recipient.acknowledged_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        recipient.acknowledged_user_agent = (request.user_agent.string or "")[:255]

        db.session.commit()

        flash("Document acknowledged. Thank you.", "success")
        return redirect(url_for("hr_documents.my_documents"))

    return render_template(
        "hr_documents/acknowledge.html",
        document=document,
        recipient=recipient,
        user=user,
    )
