from datetime import datetime, date

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from io import BytesIO
from werkzeug.utils import secure_filename

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app import db
from app.dwp import dwp_bp
from app.models import DWPRecord, User, Store, HRDocument, HRDocumentRecipient
from app.services.email_service import send_email


DISCUSSION_TYPES = [
    "Coaching",
    "Oral Reminder",
    "Written Reminder",
    "DML - Decision Making Leave",
]

LETTER_REQUIRED_TYPES = [
    "Written Reminder",
    "DML - Decision Making Leave",
]

CATEGORIES = [
    "Conduct",
    "Performance",
    "Attendance",
]

ALLOWED_LETTER_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def user_display_name(user):
    if not user:
        return "Unknown User"

    for attr in ["name", "username", "email"]:
        value = getattr(user, attr, None)
        if value:
            return value

    first_name = getattr(user, "first_name", None)
    last_name = getattr(user, "last_name", None)
    combined = " ".join([x for x in [first_name, last_name] if x])
    if combined:
        return combined

    return f"User {user.id}"


def login_required_user():
    user = current_user()
    if not user:
        abort(403)
    return user


def is_admin_like(user):
    return user.role in ["admin", "hr"]


def is_supervisor_like(user):
    return user.role == "supervisor"


def is_manager_like(user):
    return user.role in ["manager", "general_manager", "gm"]


def allowed_store_numbers_for_user(user):
    if is_admin_like(user):
        rows = (
            db.session.query(User.store_number)
            .filter(User.store_number.isnot(None))
            .distinct()
            .all()
        )
        return sorted({str(r[0]) for r in rows if r[0]})

    if is_supervisor_like(user):
        if user.area_name:
            return sorted({
                store.store_number
                for store in Store.query.filter_by(area_name=user.area_name, is_active=True).all()
                if store.store_number
            })

        stores = []

        multi_store_fields = [
            getattr(user, "stores", None),
            getattr(user, "store_numbers", None),
            getattr(user, "allowed_stores", None),
            getattr(user, "supervisor_stores", None),
        ]

        for value in multi_store_fields:
            if not value:
                continue
            if isinstance(value, str):
                for part in value.replace(";", ",").split(","):
                    if part.strip():
                        stores.append(part.strip())

        if user.store_number:
            stores.append(str(user.store_number))

        return sorted(set(stores))

    if user.store_number:
        return [str(user.store_number)]

    return []


def can_view_record(user, record):
    if is_admin_like(user):
        return True

    # The person assigned to the DWP must always be able to open their own record,
    # including supervisors who may have an area instead of a store number.
    if record.team_member_id == user.id:
        return True

    if is_supervisor_like(user):
        return str(record.store_number) in allowed_store_numbers_for_user(user)

    return str(record.store_number) == str(user.store_number)


def allowed_employee_query(user):
    query = User.query

    # Only active-ish real accounts.
    if hasattr(User, "is_active"):
        query = query.filter(User.is_active.is_(True))

    # Exclude system/admin/HR accounts from DWP target selection.
    if hasattr(User, "role"):
        query = query.filter(~User.role.in_(["admin", "hr"]))

    if is_admin_like(user):
        # Admin/HR can select any active store-level user, plus supervisors assigned to an area.
        return (
            query
            .filter(
                (
                    (User.store_number.isnot(None)) & (User.store_number != "")
                )
                | (
                    (User.role == "supervisor")
                    & (User.area_name.isnot(None))
                    & (User.area_name != "")
                )
            )
            .order_by(User.store_number.asc(), User.area_name.asc(), User.name.asc(), User.username.asc())
        )

    stores = allowed_store_numbers_for_user(user)

    if is_supervisor_like(user):
        # Supervisors can see store-level users in their area plus themselves.
        scoped_query = query.filter(
            (
                (User.store_number.in_(stores)) if stores else False
            )
            | (User.id == user.id)
        )
        return scoped_query.order_by(User.store_number.asc(), User.area_name.asc(), User.name.asc(), User.username.asc())

    if stores:
        return (
            query
            .filter(User.store_number.in_(stores))
            .order_by(User.store_number.asc(), User.area_name.asc(), User.name.asc(), User.username.asc())
        )

    return query.filter(User.id == -1)


def parse_date(value, field_name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        raise ValueError(f"{field_name} is required.")


def allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_LETTER_EXTENSIONS

def send_dwp_created_emails(record):
    team_member = User.query.get(record.team_member_id)
    submitter = User.query.get(record.submitted_by_id)

    record_url = url_for("dwp.detail", record_id=record.id, _external=True)
    tm_name = record.team_member_name_snapshot or user_display_name(team_member)
    submitter_name = record.submitted_by_name_snapshot or user_display_name(submitter)

    tm_email = team_member.get_notification_email() if team_member else None
    submitter_email = submitter.get_notification_email() if submitter else None

    sent_count = 0

    if tm_email:
        body = f"""Hi {tm_name},

A DWP record has been created for you in BPI Ops.

Store: {record.store_number}
Type: {record.discussion_type}
Category: {record.category}
Date of Conversation: {record.conversation_date.strftime('%m/%d/%Y')}
Date of Infraction: {record.infraction_date.strftime('%m/%d/%Y')}
Submitted By: {submitter_name}

Please review and acknowledge the record here:
{record_url}

Thank you,
BPI Ops
"""

        send_email(
            to_email=tm_email,
            subject=f"DWP Record Created - {record.discussion_type}",
            body=body,
        )
        sent_count += 1

    if submitter_email and submitter_email != tm_email:
        body = f"""Hi {submitter_name},

Your DWP record was submitted successfully.

Team Member: {tm_name}
Store: {record.store_number}
Type: {record.discussion_type}
Category: {record.category}
Date of Conversation: {record.conversation_date.strftime('%m/%d/%Y')}

You can view the record here:
{record_url}

Thank you,
BPI Ops
"""

        send_email(
            to_email=submitter_email,
            subject=f"DWP Submitted - {tm_name}",
            body=body,
        )
        sent_count += 1

    return sent_count




@dwp_bp.route("/my")
def my_dwp():
    user = login_required_user()

    records = (
        DWPRecord.query
        .filter(DWPRecord.team_member_id == user.id)
        .order_by(DWPRecord.acknowledged_at.isnot(None), DWPRecord.conversation_date.desc(), DWPRecord.created_at.desc())
        .all()
    )

    return render_template("dwp/my.html", records=records, user=user)


@dwp_bp.route("/")
def index():
    user = login_required_user()

    search = (request.args.get("search") or "").strip()
    store_filter = (request.args.get("store") or "").strip()
    type_filter = (request.args.get("type") or "").strip()
    category_filter = (request.args.get("category") or "").strip()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()

    query = DWPRecord.query

    allowed_stores = allowed_store_numbers_for_user(user)

    if not is_admin_like(user):
        if allowed_stores:
            query = query.filter(DWPRecord.store_number.in_(allowed_stores))
        else:
            query = query.filter(DWPRecord.submitted_by_id == user.id)

    if store_filter:
        if is_admin_like(user) or store_filter in allowed_stores:
            query = query.filter(DWPRecord.store_number == store_filter)

    if type_filter:
        query = query.filter(DWPRecord.discussion_type == type_filter)

    if category_filter:
        query = query.filter(DWPRecord.category == category_filter)

    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
            query = query.filter(DWPRecord.conversation_date >= date_from)
        except Exception:
            flash("Invalid from date.", "error")

    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
            query = query.filter(DWPRecord.conversation_date <= date_to)
        except Exception:
            flash("Invalid to date.", "error")

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                DWPRecord.team_member_name_snapshot.ilike(like),
                DWPRecord.submitted_by_name_snapshot.ilike(like),
                DWPRecord.store_number.ilike(like),
                DWPRecord.discussion_type.ilike(like),
                DWPRecord.category.ilike(like),
                DWPRecord.actual_performance.ilike(like),
                DWPRecord.expected_performance.ilike(like),
            )
        )

    records = query.order_by(DWPRecord.conversation_date.desc(), DWPRecord.created_at.desc()).limit(500).all()

    if is_admin_like(user):
        store_rows = (
            db.session.query(DWPRecord.store_number)
            .distinct()
            .order_by(DWPRecord.store_number.asc())
            .all()
        )
        store_options = [r[0] for r in store_rows if r[0]]
    else:
        store_options = allowed_stores

    stats = {
        "total": len(records),
        "coaching": sum(1 for r in records if r.discussion_type == "Coaching"),
        "oral": sum(1 for r in records if r.discussion_type == "Oral Reminder"),
        "written": sum(1 for r in records if r.discussion_type == "Written Reminder"),
        "dml": sum(1 for r in records if r.discussion_type == "DML - Decision Making Leave"),
    }

    return render_template(
        "dwp/index.html",
        records=records,
        user=user,
        search=search,
        store_filter=store_filter,
        type_filter=type_filter,
        category_filter=category_filter,
        date_from=date_from_raw,
        date_to=date_to_raw,
        store_options=store_options,
        discussion_types=DISCUSSION_TYPES,
        categories=CATEGORIES,
        stats=stats,
    )


@dwp_bp.route("/new", methods=["GET", "POST"])
def new():
    user = login_required_user()
    employees = allowed_employee_query(user).all()

    if request.method == "POST":
        try:
            conversation_date = parse_date(request.form.get("conversation_date", ""), "Date of conversation")
            infraction_date = parse_date(request.form.get("infraction_date", ""), "Date of infraction")

            team_member_id = request.form.get("team_member_id", type=int)
            discussion_type = (request.form.get("discussion_type") or "").strip()
            category = (request.form.get("category") or "").strip()

            if discussion_type not in DISCUSSION_TYPES:
                raise ValueError("Please select a valid type of discussion.")

            if category not in CATEGORIES:
                raise ValueError("Please select a valid category.")

            team_member = allowed_employee_query(user).filter(User.id == team_member_id).first()
            if not team_member:
                raise ValueError("Please select a team member from the employee list.")

            store_number = str(team_member.store_number or user.store_number or "").strip()
            if not store_number and getattr(team_member, "role", None) == "supervisor" and getattr(team_member, "area_name", None):
                # DWPRecord.store_number is varchar(10), so store supervisor area compactly.
                store_number = str(team_member.area_name or "").strip()[:10]
            if not store_number:
                raise ValueError("Selected team member must have a store number or supervisor area.")

            expected_performance = (request.form.get("expected_performance") or "").strip()
            actual_performance = (request.form.get("actual_performance") or "").strip()
            business_reason = (request.form.get("business_reason") or "").strip()
            logical_consequence = (request.form.get("logical_consequence") or "").strip()
            team_member_agrees_to = (request.form.get("team_member_agrees_to") or "").strip()

            required_text_fields = [
                (expected_performance, "Expected Performance"),
                (actual_performance, "Actual Performance"),
                (business_reason, "Good business reason"),
                (logical_consequence, "Logical consequence"),
                (team_member_agrees_to, "Team member agrees to"),
            ]

            for value, label in required_text_fields:
                if not value:
                    raise ValueError(f"{label} is required.")

            upload = request.files.get("letter_file")

            letter_filename = None
            letter_original_filename = None
            letter_content_type = None
            letter_data = None
            letter_uploaded_at = None

            if discussion_type in LETTER_REQUIRED_TYPES and (not upload or not upload.filename):
                raise ValueError("A letter upload is required for Written Reminder and DML - Decision Making Leave.")

            if upload and upload.filename:
                if not allowed_file(upload.filename):
                    raise ValueError("Letter must be a PDF, DOC, DOCX, JPG, or PNG file.")

                original_filename = upload.filename
                safe_name = secure_filename(original_filename)
                file_data = upload.read()

                if not file_data:
                    raise ValueError("Uploaded letter appears to be empty.")

                letter_filename = f"dwp_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{safe_name}"
                letter_original_filename = original_filename
                letter_content_type = upload.mimetype
                letter_data = file_data
                letter_uploaded_at = datetime.utcnow()

            record = DWPRecord(
                conversation_date=conversation_date,
                infraction_date=infraction_date,
                store_number=store_number,
                team_member_id=team_member.id,
                team_member_name_snapshot=user_display_name(team_member),
                submitted_by_id=user.id,
                submitted_by_name_snapshot=user_display_name(user),
                discussion_type=discussion_type,
                category=category,
                previous_conversations=(request.form.get("previous_conversations") or "").strip(),
                expected_performance=expected_performance,
                actual_performance=actual_performance,
                team_member_statement=(request.form.get("team_member_statement") or "").strip(),
                business_reason=business_reason,
                logical_consequence=logical_consequence,
                team_member_agrees_to=team_member_agrees_to,
                additional_comments=(request.form.get("additional_comments") or "").strip(),
                letter_filename=letter_filename,
                letter_original_filename=letter_original_filename,
                letter_content_type=letter_content_type,
                letter_data=letter_data,
                letter_uploaded_at=letter_uploaded_at,
            )

            db.session.add(record)
            db.session.commit()

            try:
                email_count = send_dwp_created_emails(record)
                if email_count:
                    flash(f"DWP record created. Email notification sent to {email_count} recipient(s).", "success")
                else:
                    flash("DWP record created. No email notification was sent because no notification email was available.", "success")
            except Exception as email_exc:
                flash(f"DWP record created, but email notification failed: {email_exc}", "warning")

            return redirect(url_for("dwp.detail", record_id=record.id))

        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:
            db.session.rollback()
            flash(f"DWP record could not be saved: {exc}", "error")

    return render_template(
        "dwp/new.html",
        employees=employees,
        discussion_types=DISCUSSION_TYPES,
        categories=CATEGORIES,
        today=date.today().isoformat(),
    )




@dwp_bp.route("/team-member/<int:user_id>")
def team_member_file(user_id):
    user = login_required_user()
    team_member = User.query.get_or_404(user_id)

    # Access control: HR/Admin all, supervisors area/store, managers own store.
    fake_record_store = str(team_member.store_number or "")
    if not is_admin_like(user):
        allowed_stores = allowed_store_numbers_for_user(user)
        if fake_record_store not in allowed_stores and str(user.store_number or "") != fake_record_store:
            abort(403)

    records = (
        DWPRecord.query
        .filter(DWPRecord.team_member_id == team_member.id)
        .order_by(DWPRecord.conversation_date.desc(), DWPRecord.created_at.desc())
        .all()
    )

    stats = {
        "total": len(records),
        "coaching": sum(1 for r in records if r.discussion_type == "Coaching"),
        "oral": sum(1 for r in records if r.discussion_type == "Oral Reminder"),
        "written": sum(1 for r in records if r.discussion_type == "Written Reminder"),
        "dml": sum(1 for r in records if r.discussion_type == "DML - Decision Making Leave"),
    }

    hr_documents = (
        HRDocumentRecipient.query
        .join(HRDocument)
        .filter(
            HRDocumentRecipient.user_id == team_member.id,
            HRDocument.is_active.is_(True),
        )
        .order_by(
            HRDocumentRecipient.status.asc(),
            HRDocument.due_date.asc().nullslast(),
            HRDocumentRecipient.assigned_at.desc(),
        )
        .all()
    )

    hr_doc_stats = {
        "total": len(hr_documents),
        "pending": sum(1 for row in hr_documents if row.status == "pending"),
        "acknowledged": sum(1 for row in hr_documents if row.status == "acknowledged"),
    }

    return render_template(
        "team_members/file.html",
        team_member=team_member,
        team_member_name=user_display_name(team_member),
        records=records,
        stats=stats,
        hr_documents=hr_documents,
        hr_doc_stats=hr_doc_stats,
        user=user,
    )


@dwp_bp.route("/<int:record_id>")
def detail(record_id):
    user = login_required_user()
    record = DWPRecord.query.get_or_404(record_id)

    if not can_view_record(user, record):
        abort(403)

    return render_template("dwp/detail.html", record=record, user=user)




@dwp_bp.route("/<int:record_id>/acknowledge", methods=["POST"])
def acknowledge(record_id):
    user = login_required_user()
    record = DWPRecord.query.get_or_404(record_id)

    if record.team_member_id != user.id and not is_admin_like(user):
        abort(403)

    if record.acknowledged_at:
        flash("This DWP record has already been acknowledged.", "info")
        return redirect(url_for("dwp.detail", record_id=record.id))

    typed_name = (request.form.get("acknowledged_name") or "").strip()
    acknowledgement_note = (request.form.get("acknowledgement_note") or "").strip()
    confirm = request.form.get("acknowledge_confirm")

    if not confirm:
        flash("Please check the acknowledgement box before submitting.", "error")
        return redirect(url_for("dwp.detail", record_id=record.id))

    if not typed_name:
        flash("Please type your name to acknowledge this record.", "error")
        return redirect(url_for("dwp.detail", record_id=record.id))

    record.acknowledged_at = datetime.utcnow()
    record.acknowledged_by_id = user.id
    record.acknowledged_name = typed_name
    record.acknowledgement_note = acknowledgement_note
    record.status = "acknowledged"

    db.session.commit()

    flash("DWP record acknowledged.", "success")
    return redirect(url_for("dwp.detail", record_id=record.id))


@dwp_bp.route("/<int:record_id>/letter")
def download_letter(record_id):
    user = login_required_user()
    record = DWPRecord.query.get_or_404(record_id)

    if not can_view_record(user, record):
        abort(403)

    if not record.letter_data:
        abort(404)

    return send_file(
        BytesIO(record.letter_data),
        mimetype=record.letter_content_type or "application/octet-stream",
        as_attachment=True,
        download_name=record.letter_original_filename or record.letter_filename or "dwp-letter",
    )


def pdf_text(value, fallback=""):
    value = value if value is not None else fallback
    value = str(value).strip()
    return value or fallback


def make_dwp_pdf(record):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    page_w, page_h = letter
    margin = 36
    left = margin
    right = page_w - margin
    top = page_h - margin

    navy = colors.HexColor("#111827")
    blue = colors.HexColor("#2563eb")
    text = colors.HexColor("#0f172a")
    muted = colors.HexColor("#64748b")
    border = colors.HexColor("#cbd5e1")
    light = colors.HexColor("#f8fafc")
    very_light = colors.HexColor("#fbfdff")

    def safe(value, fallback=""):
        value = "" if value is None else str(value)
        value = value.strip()
        return value or fallback

    def draw_round_box(x, y, w, h, fill=very_light, stroke=border, radius=7):
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(0.8)
        c.roundRect(x, y, w, h, radius, stroke=1, fill=1)

    def draw_label(x, y, label):
        c.setFillColor(muted)
        c.setFont("Helvetica-Bold", 6.8)
        c.drawString(x, y, label.upper())

    def draw_value(x, y, value, size=9.2, bold=True):
        c.setFillColor(text)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, safe(value, "—"))

    def wrap_lines(value, max_width, font_name="Helvetica", font_size=8.2, max_lines=4):
        value = safe(value, "None listed.")
        words = value.replace("\n", " \n ").split()
        lines = []
        current = ""

        for word in words:
            if word == "\n":
                if current:
                    lines.append(current)
                    current = ""
                continue

            trial = word if not current else current + " " + word
            if stringWidth(trial, font_name, font_size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word

            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]

        return lines

    def draw_text_box(x, y, w, h, label, value, max_lines=4):
        draw_round_box(x, y, w, h)
        draw_label(x + 9, y + h - 13, label)

        c.setFillColor(text)
        c.setFont("Helvetica", 8.2)
        lines = wrap_lines(value, w - 18, "Helvetica", 8.2, max_lines=max_lines)

        text_y = y + h - 27
        for line in lines:
            c.drawString(x + 9, text_y, line)
            text_y -= 10

    def draw_meta_box(x, y, w, h, label, value):
        draw_round_box(x, y, w, h, fill=light)
        draw_label(x + 8, y + h - 13, label)
        draw_value(x + 8, y + 12, value, size=9.2)

    # Header
    header_h = 70
    draw_round_box(left, top - header_h, right - left, header_h, fill=navy, stroke=navy, radius=12)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(left + 18, top - 26, "Discipline Without Punishment Record")

    c.setFont("Helvetica", 8.8)
    c.setFillColor(colors.HexColor("#dbeafe"))
    c.drawString(left + 18, top - 43, "Boston Pie, Inc. official discussion record")

    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor("#bfdbfe"))
    c.drawRightString(right - 18, top - 24, f"DWP-{record.id}")

    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#d1d5db"))
    c.drawRightString(right - 18, top - 40, f"Created {record.created_at.strftime('%m/%d/%Y %I:%M %p')}")

    # Meta
    y = top - header_h - 14
    gap = 8
    meta_h = 42
    col_w = (right - left - (gap * 2)) / 3

    draw_meta_box(left, y - meta_h, col_w, meta_h, "Date of Conversation", record.conversation_date.strftime("%m/%d/%Y"))
    draw_meta_box(left + col_w + gap, y - meta_h, col_w, meta_h, "Date of Infraction", record.infraction_date.strftime("%m/%d/%Y"))
    draw_meta_box(left + (col_w + gap) * 2, y - meta_h, col_w, meta_h, "Store", record.store_number)

    y -= meta_h + gap
    draw_meta_box(left, y - meta_h, col_w, meta_h, "Team Member", record.team_member_name_snapshot)
    draw_meta_box(left + col_w + gap, y - meta_h, col_w, meta_h, "Submitted By", record.submitted_by_name_snapshot)
    draw_meta_box(left + (col_w + gap) * 2, y - meta_h, col_w, meta_h, "Category", record.category)

    y -= meta_h + gap
    draw_meta_box(left, y - meta_h, col_w, meta_h, "Type of Discussion", record.discussion_type)
    draw_meta_box(left + col_w + gap, y - meta_h, col_w, meta_h, "Letter Attachment", record.letter_original_filename or "No letter attached")
    draw_meta_box(left + (col_w + gap) * 2, y - meta_h, col_w, meta_h, "Record ID", f"DWP-{record.id}")

    # Body boxes
    y -= meta_h + 12

    full_w = right - left
    half_w = (full_w - gap) / 2

    draw_text_box(left, y - 44, full_w, 44, "Information on Previous Conversations", record.previous_conversations or "None listed.", max_lines=2)

    y -= 52

    box_h = 62
    draw_text_box(left, y - box_h, half_w, box_h, "Expected Performance", record.expected_performance, max_lines=4)
    draw_text_box(left + half_w + gap, y - box_h, half_w, box_h, "Actual Performance", record.actual_performance, max_lines=4)

    y -= box_h + gap
    draw_text_box(left, y - box_h, half_w, box_h, "Team Member Statement / Response", record.team_member_statement or "None listed.", max_lines=4)
    draw_text_box(left + half_w + gap, y - box_h, half_w, box_h, "Good Business Reason", record.business_reason, max_lines=4)

    y -= box_h + gap
    draw_text_box(left, y - box_h, half_w, box_h, "Logical Consequences if Not Corrected", record.logical_consequence, max_lines=4)
    draw_text_box(left + half_w + gap, y - box_h, half_w, box_h, "Team Member Agrees To", record.team_member_agrees_to, max_lines=4)

    y -= box_h + gap
    draw_text_box(left, y - 44, full_w, 44, "Additional Comments", record.additional_comments or "None.", max_lines=2)

    y -= 58

    # Digital acknowledgement
    ack_text = "Pending team member acknowledgement."
    if record.acknowledged_at:
        ack_text = f"Acknowledged by {record.acknowledged_name or record.team_member_name_snapshot} on {record.acknowledged_at.strftime('%m/%d/%Y %I:%M %p')}."
        if record.acknowledgement_note:
            ack_text += f" Note: {record.acknowledgement_note}"

    draw_text_box(left, y - 50, full_w, 50, "Team Member Acknowledgement", ack_text, max_lines=3)

    # Footer
    c.setFillColor(muted)
    c.setFont("Helvetica", 6.8)
    footer = "This record documents the conversation and expectations discussed. It does not replace required company policy, HR review, or applicable legal process."
    c.drawString(left, 24, footer)
    c.drawRightString(right, 24, "Boston Pie, Inc. · Confidential HR Record")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


@dwp_bp.route("/<int:record_id>/pdf")
def download_pdf(record_id):
    user = login_required_user()
    record = DWPRecord.query.get_or_404(record_id)

    if not can_view_record(user, record):
        abort(403)

    pdf_buffer = make_dwp_pdf(record)
    filename = f"DWP-{record.store_number}-{record.team_member_name_snapshot}-Record-{record.id}.pdf"
    filename = secure_filename(filename)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
