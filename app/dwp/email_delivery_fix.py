from __future__ import annotations

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app import db
from app.dwp import routes as dwp_routes
from app.models import DWPEmailSettings, User
from app.services.module_access_service import email_event_is_enabled


DWP_SUBMITTED_EVENT_KEY = "email__dwp__submitted"


def _normalized_email(value):
    return str(value or "").strip().lower()


def send_dwp_created_emails(record):
    """Send DWP notifications without letting module access suppress HR PDFs.

    The centralized DWP email-event switch controls the normal team-member and
    submitter notifications. The dedicated DWP PDF Distribution setting remains
    authoritative for confidential PDF recipients and is evaluated separately.
    This function always returns ``(sent_count, failed_count)``.
    """
    team_member = db.session.get(User, record.team_member_id)
    submitter = db.session.get(User, record.submitted_by_id)

    record_url = url_for(
        "dwp.detail",
        record_id=record.id,
        _external=True,
    )

    tm_name = (
        record.team_member_name_snapshot
        or dwp_routes.user_display_name(team_member)
    )
    submitter_name = (
        record.submitted_by_name_snapshot
        or dwp_routes.user_display_name(submitter)
    )

    tm_email = (
        team_member.get_notification_email()
        if team_member
        else None
    )
    submitter_email = (
        submitter.get_notification_email()
        if submitter
        else None
    )

    sent_count = 0
    failed_count = 0
    delivered_email_keys = set()

    direct_notifications_enabled = email_event_is_enabled(
        DWP_SUBMITTED_EVENT_KEY
    )

    if direct_notifications_enabled:
        if tm_email:
            tm_body = f"""Hi {tm_name},

A completed DWP record has been filed for you in BPI Ops.

Store: {record.store_number}
Type: {record.discussion_type}
Category: {record.category}
Date of Conversation: {record.conversation_date.strftime('%m/%d/%Y')}
Date of Infraction: {record.infraction_date.strftime('%m/%d/%Y')}
Submitted By: {submitter_name}

You can review the completed record here:
{record_url}

Thank you,
BPI Ops
"""

            if dwp_routes.safe_send_dwp_email(
                to_email=tm_email,
                subject=(
                    f"DWP Record Created - {record.discussion_type}"
                ),
                body=tm_body,
            ):
                sent_count += 1
                delivered_email_keys.add(_normalized_email(tm_email))
            else:
                failed_count += 1

        if submitter_email and (
            not tm_email
            or _normalized_email(submitter_email)
            != _normalized_email(tm_email)
        ):
            submitter_body = f"""Hi {submitter_name},

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

            if dwp_routes.safe_send_dwp_email(
                to_email=submitter_email,
                subject=f"DWP Submitted - {tm_name}",
                body=submitter_body,
            ):
                sent_count += 1
                delivered_email_keys.add(
                    _normalized_email(submitter_email)
                )
            else:
                failed_count += 1
    else:
        current_app.logger.info(
            "DWP direct notifications disabled by Module & Email Access "
            "for record_id=%s",
            record.id,
        )

    settings = DWPEmailSettings.query.first()
    pdf_distribution_enabled = bool(
        settings and settings.enabled
    )

    if pdf_distribution_enabled:
        pdf_recipients, invalid_emails = (
            dwp_routes.parse_dwp_recipient_emails(
                settings.recipients_text
            )
        )

        if invalid_emails:
            current_app.logger.warning(
                "Ignoring invalid DWP PDF recipient emails: %s",
                ", ".join(invalid_emails),
            )

        # Only suppress a PDF duplicate when that address actually received a
        # direct notification. Failed or disabled direct delivery must not block
        # the confidential PDF distribution attempt.
        pdf_recipients = [
            email
            for email in pdf_recipients
            if _normalized_email(email)
            not in delivered_email_keys
        ]

        if pdf_recipients:
            pdf_buffer = dwp_routes.make_dwp_pdf(record)
            pdf_content = pdf_buffer.getvalue()
            pdf_filename = secure_filename(
                f"DWP-{record.store_number}-"
                f"{tm_name}-"
                f"Record-{record.id}.pdf"
            )

            pdf_body = f"""A new DWP record was submitted in BPI Ops.

Team Member: {tm_name}
Store: {record.store_number}
Type: {record.discussion_type}
Category: {record.category}
Date of Conversation: {record.conversation_date.strftime('%m/%d/%Y')}
Date of Infraction: {record.infraction_date.strftime('%m/%d/%Y')}
Submitted By: {submitter_name}
Record ID: DWP-{record.id}

The official DWP PDF is attached.

View the record in BPI Ops:
{record_url}

Confidential HR Record
Boston Pie, Inc.
"""

            attachment = {
                "filename": pdf_filename,
                "content": pdf_content,
                "mime_type": "application/pdf",
            }

            for recipient in pdf_recipients:
                if dwp_routes.safe_send_dwp_email(
                    to_email=recipient,
                    subject=(
                        "DWP PDF Submitted - "
                        f"{tm_name} - Store {record.store_number}"
                    ),
                    body=pdf_body,
                    attachments=[attachment],
                ):
                    sent_count += 1
                    delivered_email_keys.add(
                        _normalized_email(recipient)
                    )
                else:
                    failed_count += 1

    current_app.logger.info(
        "DWP email delivery completed record_id=%s "
        "direct_enabled=%s pdf_enabled=%s sent=%s failed=%s",
        record.id,
        direct_notifications_enabled,
        pdf_distribution_enabled,
        sent_count,
        failed_count,
    )

    return sent_count, failed_count


# Install the corrected implementation after app.dwp.routes has loaded.
dwp_routes.send_dwp_created_emails = send_dwp_created_emails
