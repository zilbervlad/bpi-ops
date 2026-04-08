from datetime import datetime, date, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file
from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import (
    Store,
    SVRTemplateField,
    SVRReport,
    SVRReportValue,
    MaintenanceTicket,
    WeeklyFocusItem,
)

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

svr_bp = Blueprint("svr", __name__, url_prefix="/svr")

APP_TZ = ZoneInfo("America/New_York")


def now_et():
    return datetime.now(APP_TZ)


def today_et():
    return now_et().date()


def get_svr_week_range():
    today = today_et()
    week_offset_raw = (request.args.get("week_offset") or "0").strip()

    try:
        week_offset = int(week_offset_raw)
    except ValueError:
        week_offset = 0

    current_week_start = today - timedelta(days=today.weekday())
    week_start = current_week_start + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    return week_start, week_end, week_offset


DEFAULT_SVR_TEMPLATE = [
    ("date", "Date", "text"),
    ("store_number", "Store #", "text"),
    ("manager_on_duty", "Manager on duty", "text"),
    ("restroom_notes", "Restroom notes", "textarea"),
    ("checklist_book_notes", "Checklist book notes", "textarea"),
    ("one_way_proof", "1-way proof- dough projection/dough marked inside the walk-in", "textarea"),
    ("pizza_quality_notes", "Pizza Quality notes", "textarea"),
    ("load_and_go", "Load & Go- certified load captain on the schedule for every rush", "yesno"),
    ("last_week_svr_review", "Last week's SVR review", "textarea"),
    ("outside_store_condition_notes", "Outside store condition notes", "textarea"),
    ("carry_out_notes", "Carry out notes", "textarea"),
    ("store_condition_notes", "Store condition notes", "textarea"),
    ("refrigeration_units_notes", "Refrigeration units notes", "textarea"),
    ("bakewares_notes", "Bake wares notes", "textarea"),
    ("oven_heatrack_notes", "Oven/heatrack notes", "textarea"),
    ("callout_calendar_notes", "Call out calendar notes- who needs a meeting?", "textarea"),
    ("deposit_log_notes", "Deposit Log- which days are missing?", "textarea"),
    ("pest_control_notes", "Pest Control", "textarea"),
    ("cleaning_list_for_week", "Cleaning list for the week", "textarea"),
    ("goals_for_week", "Goals for the week", "textarea"),
    ("maintenance_needs", "Maintenance needs", "textarea"),
]


def get_supervisor_visible_stores():
    role = session.get("user_role")
    user_area = session.get("user_area")

    if role == "admin":
        return Store.query.filter_by(is_active=True).order_by(Store.store_number.asc()).all()

    if role == "supervisor":
        return Store.query.filter_by(
            area_name=user_area,
            is_active=True
        ).order_by(Store.store_number.asc()).all()

    return []


def split_lines(text: str):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def sync_maintenance_from_svr(report: SVRReport):
    maintenance_value = None

    for value in report.values:
        if value.field_key == "maintenance_needs":
            maintenance_value = (value.value_text or "").strip()
            break

    existing_tickets = MaintenanceTicket.query.filter_by(
        svr_report_id=report.id,
        source_type="svr"
    ).all()

    for ticket in existing_tickets:
        db.session.delete(ticket)

    maintenance_lines = split_lines(maintenance_value)

    for line in maintenance_lines:
        db.session.add(
            MaintenanceTicket(
                store_number=report.store_number,
                title=line,
                details=f"Created from SVR #{report.id}",
                source_type="svr",
                svr_report_id=report.id,
                status="open",
            )
        )

    db.session.commit()


def sync_weekly_focus_from_svr(report: SVRReport):
    cleaning_value = ""
    goals_value = ""

    for value in report.values:
        if value.field_key == "cleaning_list_for_week":
            cleaning_value = (value.value_text or "").strip()
        elif value.field_key == "goals_for_week":
            goals_value = (value.value_text or "").strip()

    existing_items = WeeklyFocusItem.query.filter_by(
        store_number=report.store_number,
        source_type="svr"
    ).all()

    for item in existing_items:
        db.session.delete(item)

    for line in split_lines(cleaning_value):
        db.session.add(
            WeeklyFocusItem(
                store_number=report.store_number,
                item_type="cleaning",
                item_text=line,
                is_completed=False,
                source_type="svr",
                svr_report_id=report.id,
            )
        )

    for line in split_lines(goals_value):
        db.session.add(
            WeeklyFocusItem(
                store_number=report.store_number,
                item_type="goal",
                item_text=line,
                is_completed=False,
                source_type="svr",
                svr_report_id=report.id,
            )
        )

    db.session.commit()


def ensure_default_svr_template():
    existing_fields = {f.field_key: f for f in SVRTemplateField.query.all()}
    active_keys = {key for key, _, _ in DEFAULT_SVR_TEMPLATE}

    ordered_fields = []

    for sort_order, (field_key, field_label, field_type) in enumerate(DEFAULT_SVR_TEMPLATE, start=1):
        if field_key in existing_fields:
            field = existing_fields[field_key]
            field.field_label = field_label
            field.field_type = field_type
            field.sort_order = sort_order
            field.is_active = True
        else:
            field = SVRTemplateField(
                field_key=field_key,
                field_label=field_label,
                field_type=field_type,
                sort_order=sort_order,
                is_active=True,
            )
            db.session.add(field)

        ordered_fields.append(field)

    for field in existing_fields.values():
        if field.field_key not in active_keys:
            field.is_active = False

    db.session.commit()
    return ordered_fields


def build_report_context(report: SVRReport):
    values = sorted(report.values, key=lambda x: (x.sort_order, x.id))

    manager_summary = {
        "store_number": report.store_number,
        "visit_date": report.visit_date.strftime("%B %d, %Y"),
        "supervisor_name": report.supervisor_name or "—",
        "manager_on_duty": report.manager_on_duty or "—",
        "cleaning_list_for_week": "",
        "goals_for_week": "",
    }

    for value in values:
        if value.field_key == "cleaning_list_for_week":
            manager_summary["cleaning_list_for_week"] = value.value_text or ""
        elif value.field_key == "goals_for_week":
            manager_summary["goals_for_week"] = value.value_text or ""

    current_action_items = WeeklyFocusItem.query.filter_by(
        store_number=report.store_number,
        source_type="svr"
    ).order_by(
        WeeklyFocusItem.is_completed.asc(),
        WeeklyFocusItem.item_type.asc(),
        WeeklyFocusItem.id.asc()
    ).all()

    open_action_items = [item for item in current_action_items if not item.is_completed]
    completed_action_items = [item for item in current_action_items if item.is_completed]

    return values, manager_summary, open_action_items, completed_action_items


def generate_svr_pdf(report, values, manager_summary, open_action_items, completed_action_items):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "SVRTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=8,
    )

    section_style = ParagraphStyle(
        "SVRSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=10,
        spaceAfter=8,
    )

    label_style = ParagraphStyle(
        "SVRLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#64748b"),
    )

    value_style = ParagraphStyle(
        "SVRValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0f172a"),
    )

    small_style = ParagraphStyle(
        "SVRSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )

    story = []

    header_table = Table(
        [[
            Paragraph("BPI Ops SVR Report", title_style),
            Paragraph(
                f"SVR #{report.id}",
                ParagraphStyle(
                    "HeaderPill",
                    parent=styles["Normal"],
                    fontName="Helvetica-Bold",
                    fontSize=10,
                    textColor=colors.HexColor("#1e3a8a"),
                    alignment=TA_LEFT,
                )
            )
        ]],
        colWidths=[5.8 * inch, 1.1 * inch],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef4ff")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#c7d2fe")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 12))

    info_table = Table([
        [
            Paragraph("<b>Store</b><br/>" + (report.store_number or "—"), value_style),
            Paragraph("<b>Date</b><br/>" + report.visit_date.strftime("%B %d, %Y"), value_style),
            Paragraph("<b>Supervisor</b><br/>" + (report.supervisor_name or "—"), value_style),
            Paragraph("<b>Manager</b><br/>" + (report.manager_on_duty or "—"), value_style),
        ]
    ], colWidths=[1.6 * inch, 1.9 * inch, 1.9 * inch, 1.7 * inch])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dbe3ee")),
        ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12))

    metrics_table = Table([
        [
            Paragraph("<b>Open Action Items</b><br/><font size='20'>%s</font>" % len(open_action_items), value_style),
            Paragraph("<b>Completed Action Items</b><br/><font size='20'>%s</font>" % len(completed_action_items), value_style),
        ]
    ], colWidths=[3.6 * inch, 3.6 * inch])
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#fff1f2")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#eff6ff")),
        ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#fda4af")),
        ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#93c5fd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Full SVR", section_style))

    field_rows = []
    for value in values:
        display_value = value.value_text or "—"
        field_rows.append([
            Paragraph(value.field_label, label_style),
            Paragraph(display_value.replace("\n", "<br/>"), value_style),
        ])

    field_table = Table(field_rows, colWidths=[2.15 * inch, 4.85 * inch], repeatRows=0)
    field_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dbe3ee")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(field_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Manager Weekly Focus Summary", section_style))

    summary_rows = [
        [Paragraph("Store", label_style), Paragraph(manager_summary["store_number"], value_style)],
        [Paragraph("Date", label_style), Paragraph(manager_summary["visit_date"], value_style)],
        [Paragraph("Supervisor", label_style), Paragraph(manager_summary["supervisor_name"], value_style)],
        [Paragraph("Manager on Duty", label_style), Paragraph(manager_summary["manager_on_duty"], value_style)],
        [Paragraph("Cleaning List for the Week", label_style), Paragraph((manager_summary["cleaning_list_for_week"] or "—").replace("\n", "<br/>"), value_style)],
        [Paragraph("Goals for the Week", label_style), Paragraph((manager_summary["goals_for_week"] or "—").replace("\n", "<br/>"), value_style)],
    ]

    summary_table = Table(summary_rows, colWidths=[2.15 * inch, 4.85 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafcff")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dbe3ee")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Current Open Action Items", section_style))
    if open_action_items:
        open_rows = [[
            Paragraph("<b>Type</b>", small_style),
            Paragraph("<b>Item</b>", small_style),
        ]]
        for item in open_action_items:
            open_rows.append([
                Paragraph(item.item_type.title(), value_style),
                Paragraph(item.item_text, value_style),
            ])

        open_table = Table(open_rows, colWidths=[1.3 * inch, 5.7 * inch], repeatRows=1)
        open_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fff7ed")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#9a3412")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#fed7aa")),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#fde7cf")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(open_table)
    else:
        story.append(Paragraph("No open action items for this store.", small_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Completed Action Items", section_style))
    if completed_action_items:
        completed_rows = [[
            Paragraph("<b>Type</b>", small_style),
            Paragraph("<b>Item</b>", small_style),
            Paragraph("<b>Completed</b>", small_style),
        ]]
        for item in completed_action_items:
            completed_rows.append([
                Paragraph(item.item_type.title(), value_style),
                Paragraph(item.item_text, value_style),
                Paragraph(
                    item.completed_at.strftime("%b %d, %Y %I:%M %p") if item.completed_at else "—",
                    value_style
                ),
            ])

        completed_table = Table(completed_rows, colWidths=[1.1 * inch, 4.2 * inch, 1.7 * inch], repeatRows=1)
        completed_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#bfdbfe")),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbeafe")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(completed_table)
    else:
        story.append(Paragraph("No completed action items yet.", small_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


@svr_bp.route("/")
@login_required
@role_required("admin", "supervisor")
def index():
    stores = get_supervisor_visible_stores()
    visible_store_numbers = {store.store_number for store in stores}

    reports = SVRReport.query.order_by(
        SVRReport.visit_date.desc(),
        SVRReport.created_at.desc()
    ).all()

    reports = [r for r in reports if r.store_number in visible_store_numbers]

    week_start, week_end, week_offset = get_svr_week_range()
    week_label = f"{week_start.strftime('%m/%d/%Y')} – {week_end.strftime('%m/%d/%Y')}"

    weekly_reports = [
        r for r in reports
        if r.visit_date and week_start <= r.visit_date <= week_end
    ]
    weekly_report_store_numbers = {r.store_number for r in weekly_reports}

    total_stores = len(stores)
    submitted_this_week = len(weekly_report_store_numbers)
    missing_this_week = max(total_stores - submitted_this_week, 0)
    overall_compliance = round((submitted_this_week / total_stores) * 100, 1) if total_stores else 0.0

    stores_by_area = {}
    for store in stores:
        stores_by_area.setdefault(store.area_name, []).append(store)

    area_summary_rows = []
    areas_fully_complete = 0

    for area_name, area_stores in sorted(stores_by_area.items()):
        area_store_numbers = {store.store_number for store in area_stores}
        submitted_count = len(area_store_numbers & weekly_report_store_numbers)
        store_count = len(area_stores)
        missing_count = max(store_count - submitted_count, 0)
        compliance = round((submitted_count / store_count) * 100, 1) if store_count else 0.0

        if missing_count == 0 and store_count > 0:
            areas_fully_complete += 1

        missing_store_numbers = sorted(list(area_store_numbers - weekly_report_store_numbers))

        area_summary_rows.append({
            "area_name": area_name,
            "store_count": store_count,
            "submitted_count": submitted_count,
            "missing_count": missing_count,
            "missing_store_numbers": missing_store_numbers,
            "compliance": compliance,
        })

    return render_template(
        "svr_list.html",
        reports=weekly_reports,
        stores=stores,
        submitted_this_week=submitted_this_week,
        missing_this_week=missing_this_week,
        areas_fully_complete=areas_fully_complete,
        overall_compliance=overall_compliance,
        area_summary_rows=area_summary_rows,
        week_start=week_start,
        week_end=week_end,
        week_offset=week_offset,
        week_label=week_label,
    )


@svr_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("supervisor")
def new_report():
    stores = get_supervisor_visible_stores()
    fields = ensure_default_svr_template()

    if not stores:
        flash("No stores assigned to this supervisor.", "error")
        return redirect(url_for("svr.index"))

    default_store = stores[0].store_number
    selected_store = request.args.get("store", default_store).strip()

    allowed_store_numbers = {store.store_number for store in stores}
    if selected_store not in allowed_store_numbers:
        selected_store = default_store

    if request.method == "POST":
        store_number = request.form.get("store_number", "").strip()
        if store_number not in allowed_store_numbers:
            flash("Invalid store selection.", "error")
            return redirect(url_for("svr.new_report"))

        visit_date_raw = request.form.get("visit_date", "").strip()
        try:
            visit_date = datetime.strptime(visit_date_raw, "%Y-%m-%d").date() if visit_date_raw else today_et()
        except ValueError:
            flash("Invalid date.", "error")
            return redirect(url_for("svr.new_report", store=store_number))

        manager_on_duty = request.form.get("manager_on_duty", "").strip()

        report = SVRReport(
            store_number=store_number,
            visit_date=visit_date,
            manager_on_duty=manager_on_duty,
            supervisor_name=session.get("user_name"),
            created_by_user_id=session.get("user_id"),
        )
        db.session.add(report)
        db.session.flush()

        for field in fields:
            if field.field_key == "store_number":
                value_text = store_number
            elif field.field_key == "date":
                value_text = visit_date.strftime("%Y-%m-%d")
            elif field.field_key == "manager_on_duty":
                value_text = manager_on_duty
            else:
                value_text = request.form.get(field.field_key, "").strip()

            db.session.add(
                SVRReportValue(
                    report_id=report.id,
                    template_field_id=field.id,
                    field_key=field.field_key,
                    field_label=field.field_label,
                    field_type=field.field_type,
                    sort_order=field.sort_order,
                    value_text=value_text,
                )
            )

        db.session.commit()
        sync_maintenance_from_svr(report)
        sync_weekly_focus_from_svr(report)

        flash("SVR saved successfully.", "success")
        return redirect(url_for("svr.view_report", report_id=report.id))

    return render_template(
        "svr_form.html",
        stores=stores,
        fields=fields,
        selected_store=selected_store,
        today=today_et().strftime("%Y-%m-%d"),
    )


@svr_bp.route("/<int:report_id>")
@login_required
@role_required("admin", "supervisor")
def view_report(report_id):
    report = SVRReport.query.get_or_404(report_id)
    visible_store_numbers = {store.store_number for store in get_supervisor_visible_stores()}

    if report.store_number not in visible_store_numbers:
        flash("You do not have access to that SVR.", "error")
        return redirect(url_for("svr.index"))

    values, manager_summary, open_action_items, completed_action_items = build_report_context(report)

    return render_template(
        "svr_view.html",
        report=report,
        values=values,
        manager_summary=manager_summary,
        open_action_items=open_action_items,
        completed_action_items=completed_action_items,
    )


@svr_bp.route("/<int:report_id>/export-pdf")
@login_required
@role_required("admin", "supervisor")
def export_pdf(report_id):
    report = SVRReport.query.get_or_404(report_id)
    visible_store_numbers = {store.store_number for store in get_supervisor_visible_stores()}

    if report.store_number not in visible_store_numbers:
        flash("You do not have access to that SVR.", "error")
        return redirect(url_for("svr.index"))

    values, manager_summary, open_action_items, completed_action_items = build_report_context(report)
    pdf_buffer = generate_svr_pdf(
        report,
        values,
        manager_summary,
        open_action_items,
        completed_action_items
    )

    filename = f"SVR_{report.store_number}_{report.visit_date.strftime('%Y%m%d')}.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@svr_bp.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if action == "create":
            field_key = request.form.get("field_key", "").strip()
            field_label = request.form.get("field_label", "").strip()
            field_type = request.form.get("field_type", "textarea").strip()
            sort_order_raw = request.form.get("sort_order", "999").strip()

            if not field_key or not field_label:
                flash("Field key and label are required.", "error")
                return redirect(url_for("svr.admin"))

            try:
                sort_order = int(sort_order_raw)
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("svr.admin"))

            existing = SVRTemplateField.query.filter_by(field_key=field_key).first()
            if existing:
                flash("That field key already exists.", "error")
                return redirect(url_for("svr.admin"))

            db.session.add(
                SVRTemplateField(
                    field_key=field_key,
                    field_label=field_label,
                    field_type=field_type,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            db.session.commit()
            flash("SVR field created.", "success")
            return redirect(url_for("svr.admin"))

        if action == "update":
            field_id = request.form.get("field_id", "").strip()
            field = SVRTemplateField.query.get(field_id)

            if not field:
                flash("Field not found.", "error")
                return redirect(url_for("svr.admin"))

            field.field_key = request.form.get("field_key", "").strip()
            field.field_label = request.form.get("field_label", "").strip()
            field.field_type = request.form.get("field_type", "textarea").strip()

            try:
                field.sort_order = int(request.form.get("sort_order", "999").strip())
            except ValueError:
                flash("Sort order must be a number.", "error")
                return redirect(url_for("svr.admin"))

            field.is_active = request.form.get("is_active") == "on"

            db.session.commit()
            flash("SVR field updated.", "success")
            return redirect(url_for("svr.admin"))

    fields = SVRTemplateField.query.order_by(
        SVRTemplateField.sort_order.asc(),
        SVRTemplateField.id.asc()
    ).all()

    return render_template("svr_admin.html", fields=fields)


@svr_bp.route("/delete/<int:report_id>", methods=["POST"])
@login_required
@role_required("admin", "supervisor")
def delete_report(report_id):
    report = SVRReport.query.get_or_404(report_id)

    visible_store_numbers = {store.store_number for store in get_supervisor_visible_stores()}

    if report.store_number not in visible_store_numbers:
        flash("You do not have access to delete that SVR.", "error")
        return redirect(url_for("svr.index"))

    linked_tickets = MaintenanceTicket.query.filter_by(
        svr_report_id=report.id,
        source_type="svr"
    ).all()

    for ticket in linked_tickets:
        ticket.svr_report_id = None
        if ticket.details:
            ticket.details = f"{ticket.details} | Original SVR was deleted"
        else:
            ticket.details = "Original SVR was deleted"

    WeeklyFocusItem.query.filter_by(svr_report_id=report.id).delete()
    SVRReportValue.query.filter_by(report_id=report.id).delete()

    db.session.delete(report)
    db.session.commit()

    flash("SVR deleted.", "success")
    return redirect(url_for("svr.index"))