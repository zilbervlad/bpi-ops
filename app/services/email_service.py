import os
import smtplib
from email.message import EmailMessage


def _normalize_email_list(value):
    if not value:
        return []

    if isinstance(value, str):
        raw_values = value.replace(";", ",").split(",")
    else:
        raw_values = value

    return [email.strip() for email in raw_values if email and email.strip()]


def send_email(to_email: str, subject: str, body: str, cc_emails=None, attachments=None):
    if not to_email:
        raise ValueError("Missing recipient email address.")

    email_host = os.getenv("EMAIL_HOST", "").strip()
    email_port = int(os.getenv("EMAIL_PORT", "587"))
    email_user = os.getenv("EMAIL_USER", "").strip()
    email_password = os.getenv("EMAIL_PASSWORD", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip() or email_user

    if not email_host or not email_user or not email_password or not email_from:
        raise ValueError(
            "Email settings are missing. Check EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD, and EMAIL_FROM."
        )

    to_emails = _normalize_email_list(to_email)
    cc_list = _normalize_email_list(cc_emails)

    if not to_emails:
        raise ValueError("Missing recipient email address.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(to_emails)

    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    msg.set_content(body)

    for attachment in attachments or []:
        filename = attachment.get("filename") or "attachment"
        content = attachment.get("content") or b""
        mime_type = attachment.get("mime_type") or "application/octet-stream"

        if not content:
            continue

        if "/" in mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        msg.add_attachment(
            content,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    recipients = to_emails + cc_list

    with smtplib.SMTP(email_host, email_port) as server:
        server.starttls()
        server.login(email_user, email_password)
        server.send_message(msg, to_addrs=recipients)

    return True



def send_bulk_emails(messages):
    """
    Send multiple plain-text emails using one SMTP connection.

    messages item format:
    {
        "to_email": "...",
        "subject": "...",
        "body": "...",
        "cc_emails": optional,
        "attachments": optional,
    }

    Returns:
    {
        "sent": int,
        "failed": int,
        "errors": [{"to_email": "...", "error": "..."}]
    }
    """
    email_host = os.getenv("EMAIL_HOST", "").strip()
    email_port = int(os.getenv("EMAIL_PORT", "587"))
    email_user = os.getenv("EMAIL_USER", "").strip()
    email_password = os.getenv("EMAIL_PASSWORD", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip() or email_user

    if not email_host or not email_user or not email_password or not email_from:
        raise ValueError(
            "Email settings are missing. Check EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD, and EMAIL_FROM."
        )

    sent = 0
    failed = 0
    errors = []

    with smtplib.SMTP(email_host, email_port) as server:
        server.starttls()
        server.login(email_user, email_password)

        for item in messages:
            to_email = item.get("to_email")
            subject = item.get("subject") or ""
            body = item.get("body") or ""
            cc_emails = item.get("cc_emails")
            attachments = item.get("attachments") or []

            try:
                to_emails = _normalize_email_list(to_email)
                cc_list = _normalize_email_list(cc_emails)

                if not to_emails:
                    raise ValueError("Missing recipient email address.")

                msg = EmailMessage()
                msg["Subject"] = subject
                msg["From"] = email_from
                msg["To"] = ", ".join(to_emails)

                if cc_list:
                    msg["Cc"] = ", ".join(cc_list)

                msg.set_content(body)

                for attachment in attachments:
                    filename = attachment.get("filename") or "attachment"
                    content = attachment.get("content") or b""
                    mime_type = attachment.get("mime_type") or "application/octet-stream"

                    if not content:
                        continue

                    if "/" in mime_type:
                        maintype, subtype = mime_type.split("/", 1)
                    else:
                        maintype, subtype = "application", "octet-stream"

                    msg.add_attachment(
                        content,
                        maintype=maintype,
                        subtype=subtype,
                        filename=filename,
                    )

                recipients = to_emails + cc_list
                server.send_message(msg, to_addrs=recipients)
                sent += 1

            except Exception as exc:
                failed += 1
                errors.append({
                    "to_email": to_email,
                    "error": str(exc),
                })

    return {
        "sent": sent,
        "failed": failed,
        "errors": errors,
    }
