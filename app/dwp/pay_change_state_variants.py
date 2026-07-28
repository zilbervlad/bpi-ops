from io import BytesIO
import re
from xml.sax.saxutils import escape

from flask import render_template, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

from app.dwp import dwp_bp
from app.dwp import hr_forms, hr_forms_pdf_email


def _is_ma_store(store_number):
    return str(store_number or "").strip().startswith("37")


NON_MA_NOTICE = (
    "I understand that I am employed in an occupation in which employees customarily and regularly receive tips. "
    "I acknowledge that, for my work as a delivery driver, I will receive a cash hourly wage of the amount shown on this form per hour, "
    "and that a tip credit of the amount shown on this form per hour will be taken toward my wages for my hours worked related to deliveries.\n\n"
    "The tip credit will be the difference between my cash hourly driving wage and the applicable state or federal minimum wage. "
    "If the state or federal minimum wage increases, the tip credit will be the difference between my cash hourly driving wage and the new minimum wage. "
    "All tips received by a tip-credit employee will be retained by the employee. I understand that the Company does not permit tip pools. "
    "In no event will the Company take a tip credit that exceeds the actual tips received by the tipped employee. "
    "The tip credit will not apply to an employee who has not been informed of the tip credit provisions.\n\n"
    "I understand the law requires that employees report 100% of the tips they received. I am required to truthfully report all tips received. "
    "In the event I do not do so, I will be subjecting myself and the company to potential tax and other liability. Failure to properly report tips "
    "will result in corrective action, up to and including termination. The Company reserves the right, in its sole discretion, to modify or discontinue "
    "the use of tip credit at any time, and in any manner, it deems necessary or appropriate. THIS IS NOT A CONTRACT. "
    "This does not alter the at-will employment relationship between the Company and its employees.\n\n"
    "By signing this document, I acknowledge that I have received, read and understand this document. If I have any questions about this tip credit "
    "I will speak to my Coach, Payroll (payroll@bostonpie.com) or Human Resources (hr@bostonpie.com)."
)


def _non_ma_review_html(form):
    data = form.data
    wage = escape(str(data.get("cash_hourly_wage") or "—"))
    credit = escape(str(data.get("tip_credit") or "—"))
    paragraphs = "".join(f"<p>{escape(p)}</p>" for p in NON_MA_NOTICE.split("\n\n"))
    return (
        '<div class="section legal-box"><h3>General Managers Note</h3>'
        '<p>General Managers’ rates may fluctuate above and back down to the set pay rate based on store OER scores, '
        'but will never drop below the set rate, as agreed to herein, without written consent.</p>'
        '<h3>Federal Tip Credit Notification for Tipped Employees — Non-MA Stores</h3>'
        f'<p><strong>Cash hourly driving wage:</strong> {wage} per hour · <strong>Tip credit:</strong> {credit} per hour</p>'
        f'{paragraphs}</div>'
    )


@dwp_bp.after_request
def apply_pay_change_state_variant(response):
    if response.status_code != 200 or not response.content_type.startswith("text/html"):
        return response

    html = response.get_data(as_text=True)

    if request.endpoint == "dwp.hr_pay_change_new" and request.method == "GET":
        html = html.replace('<div class="legal-box">', '<div class="legal-box" id="maNotice">', 1)
        non_ma = render_template("hr_forms/_non_ma_tip_credit_notice.html")
        marker = '<div class="field"><label>Manager/Supervisor Signature</label>'
        html = html.replace(marker, non_ma + "\n" + marker, 1)
        html = html.replace(
            'data-id="{{ employee.id }}"',
            'data-id="{{ employee.id }}"',
        )
        script = r'''
<script>
document.addEventListener("DOMContentLoaded", function () {
  const ma = document.getElementById("maNotice");
  const nonMa = document.getElementById("nonMaNotice");
  const results = Array.from(document.querySelectorAll(".tm-result"));
  if (!ma || !nonMa) return;
  ma.style.display = "none";
  nonMa.style.display = "none";
  results.forEach(function (row) {
    row.addEventListener("click", function () {
      const text = row.textContent || "";
      const match = text.match(/Store\s+(\d+)/i);
      const store = match ? match[1] : "";
      const isMa = store.indexOf("37") === 0;
      ma.style.display = isMa ? "block" : "none";
      nonMa.style.display = isMa ? "none" : "block";
    });
  });
});
</script>
'''
        html = html.replace("</body>", script + "\n</body>", 1)
        response.set_data(html)
        response.headers["Content-Length"] = len(response.get_data())
        return response

    if request.endpoint == "dwp.hr_form_detail":
        form_id = (request.view_args or {}).get("form_id")
        form = hr_forms.HRFormRequest.query.get(form_id) if form_id else None
        if form and form.form_type == "pay_change" and not _is_ma_store(form.store_number):
            pattern = re.compile(
                r'<div class="section legal-box"><h3>General Managers Note</h3>.*?</div>',
                re.DOTALL,
            )
            html = pattern.sub(_non_ma_review_html(form), html, count=1)
            response.set_data(html)
            response.headers["Content-Length"] = len(response.get_data())

    return response


_original_make_hr_form_pdf = hr_forms_pdf_email.make_hr_form_pdf


def _make_non_ma_pay_change_pdf(form):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="LegalSmall", parent=styles["BodyText"], fontSize=8, leading=10.5))
    title = "Position / Rate / Store Change Form — Non-MA Stores"
    filename = secure_filename(f"{title}-{hr_forms._name(form.subject_user)}-{form.id}.pdf")
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=.55*inch, leftMargin=.55*inch, topMargin=.45*inch, bottomMargin=.45*inch, title=title)
    data = form.data
    story = [Paragraph("Boston Pie, Inc.", styles["BodyText"]), Paragraph(title, styles["Title"]), Spacer(1, 8)]
    rows = [
        ["Team Member", escape(hr_forms._name(form.subject_user))], ["Store", escape(str(form.store_number or "—"))],
        ["Change Type", escape(str(data.get("change_type") or "—").replace("_", " ").title())], ["Effective Date", escape(str(data.get("effective_date") or "—"))],
        ["Position From", escape(str(data.get("from_position") or "—"))], ["Position To", escape(str(data.get("to_position") or "—"))],
        ["Rate From", escape(str(data.get("from_rate") or "—"))], ["Rate To", escape(str(data.get("to_rate") or "—"))],
        ["Store From", escape(str(data.get("from_store") or "—"))], ["Store To", escape(str(data.get("to_store") or "—"))],
        ["Reason", escape(str(data.get("reason") or "—").replace("_", " ").title())], ["Comments", escape(str(data.get("comments") or "None"))],
        ["Cash Hourly Driving Wage", escape(str(data.get("cash_hourly_wage") or "—"))], ["Tip Credit", escape(str(data.get("tip_credit") or "—"))],
        ["Manager / Supervisor Signature", escape(str(data.get("manager_signature") or "—"))], ["Team Member Signature", escape(str(data.get("team_member_signature") or "Pending acknowledgement"))],
    ]
    table = Table(rows, colWidths=[2.05*inch, 4.55*inch])
    table.setStyle(TableStyle([("BACKGROUND", (0,0),(0,-1), colors.HexColor("#f1f5f9")), ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"), ("GRID",(0,0),(-1,-1),.5,colors.HexColor("#cbd5e1")), ("FONTSIZE",(0,0),(-1,-1),8.5), ("VALIGN",(0,0),(-1,-1),"TOP"), ("PADDING",(0,0),(-1,-1),6)]))
    story.extend([table, Spacer(1, 10), Paragraph("GENERAL MANAGERS NOTE", styles["Heading3"]), Paragraph("General Managers’ rates may fluctuate above and back down to the set pay rate based on store OER scores, but will never drop below the set rate, as agreed to herein, without written consent.", styles["LegalSmall"]), Spacer(1, 8), Paragraph("FEDERAL TIP CREDIT NOTIFICATION FOR TIPPED EMPLOYEES — NON-MA STORES", styles["Heading3"])])
    for paragraph in NON_MA_NOTICE.split("\n\n"):
        story.extend([Paragraph(escape(paragraph), styles["LegalSmall"]), Spacer(1, 5)])
    story.append(Paragraph("Confidential HR Record · Generated by BPI Ops", styles["LegalSmall"]))
    doc.build(story)
    content = buffer.getvalue()
    buffer.close()
    return {"filename": filename, "content": content, "mime_type": "application/pdf"}


def make_hr_form_pdf_by_state(form):
    if form.form_type == "pay_change" and not _is_ma_store(form.store_number):
        return _make_non_ma_pay_change_pdf(form)
    return _original_make_hr_form_pdf(form)


hr_forms_pdf_email.make_hr_form_pdf = make_hr_form_pdf_by_state
