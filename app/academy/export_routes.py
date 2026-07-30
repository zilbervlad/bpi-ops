from .routes_shared import *

@mit_sts_bp.route("/export/<int:mit_id>")
@login_required
def export_tasks_pdf(mit_id):
    profile = MITProfile.query.get_or_404(mit_id)

    if not is_coach() and profile.user_id != current_user.id:
        return redirect(url_for("mit_sts.dashboard"))

    # ✅ ONLY ACTIVE / ASSIGNED TASKS
    tasks = (
        MITTask.query
        .filter(
            MITTask.mit_profile_id == mit_id,
            MITTask.status.in_(["open", "in_progress", "submitted"]),
        )
        .order_by(
            MITTask.due_date.asc().nullslast(),
            MITTask.id.asc(),
        )
        .all()
    )

    total_tasks = len(tasks)

    overdue_count = sum(
        1 for t in tasks
        if t.due_date and t.due_date < date.today()
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    title_style = styles["Heading1"]
    title_style.fontName = "Helvetica-Bold"
    title_style.fontSize = 20
    title_style.leading = 24
    title_style.textColor = colors.HexColor("#0F172A")

    section_title_style = styles["Heading2"]
    section_title_style.fontName = "Helvetica-Bold"
    section_title_style.fontSize = 12
    section_title_style.leading = 14
    section_title_style.textColor = colors.HexColor("#334155")
    section_title_style.spaceAfter = 8

    body_style = styles["BodyText"]
    body_style.fontName = "Helvetica"
    body_style.fontSize = 9
    body_style.leading = 12
    body_style.textColor = colors.HexColor("#334155")

    small_style = ParagraphStyle(
        "SmallStyle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )

    label_style = ParagraphStyle(
        "LabelStyle",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )

    story = []

    mit_name = profile.mit_user.name if profile.mit_user else f"MIT #{profile.id}"
    coach_name = profile.coach_user.name if profile.coach_user else "Not assigned"
    store_number = profile.store_number or "-"
    current_level = profile.current_level or "-"
    status_value = profile.sts_status.replace("_", " ").title() if profile.sts_status else "-"

    story.append(Paragraph("Boston Pie Academy", small_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Assigned Task Report", title_style))
    story.append(Spacer(1, 12))

    info_data = [
        [
            Paragraph("<b>MIT</b><br/>" + mit_name, body_style),
            Paragraph("<b>Store</b><br/>" + str(store_number), body_style),
            Paragraph("<b>Coach</b><br/>" + coach_name, body_style),
        ],
        [
            Paragraph("<b>Current Level</b><br/>" + str(current_level), body_style),
            Paragraph("<b>STS Status</b><br/>" + status_value, body_style),
            Paragraph("<b>Report Date</b><br/>" + date.today().strftime("%Y-%m-%d"), body_style),
        ],
    ]

    info_table = Table(info_data, colWidths=[170, 170, 170])
    info_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    story.append(info_table)
    story.append(Spacer(1, 16))

    # ✅ SIMPLIFIED SUMMARY
    story.append(Paragraph("Task Summary", section_title_style))

    summary_data = [[
        Paragraph("<b>Total Assigned</b><br/>" + str(total_tasks), body_style),
        Paragraph("<b>Overdue</b><br/>" + str(overdue_count), body_style),
    ]]

    summary_table = Table(summary_data, colWidths=[260, 260])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    story.append(summary_table)
    story.append(Spacer(1, 18))

    story.append(Paragraph("Assigned Tasks", section_title_style))

    if tasks:
        task_rows = [[
            Paragraph("<b>Task</b>", label_style),
            Paragraph("<b>Status</b>", label_style),
            Paragraph("<b>Priority</b>", label_style),
            Paragraph("<b>Due</b>", label_style),
            Paragraph("<b>Notes</b>", label_style),
        ]]

        for task in tasks:
            task_rows.append([
                Paragraph(task.title or "-", body_style),
                Paragraph(task.status.replace("_", " ").title(), body_style),
                Paragraph((task.priority or "-").title(), body_style),
                Paragraph(task.due_date.strftime("%Y-%m-%d") if task.due_date else "-", body_style),
                Paragraph(task.notes or "-", body_style),
            ])

        task_table = Table(task_rows, colWidths=[185, 80, 70, 70, 123], repeatRows=1)
        task_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ]))

        story.append(task_table)
    else:
        story.append(Paragraph("No assigned tasks found.", body_style))

    doc.build(story)

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="assigned_tasks.pdf",
        mimetype="application/pdf",
    )