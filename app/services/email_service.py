import os
import smtplib
from email.mime.text import MIMEText


def send_email(to_email: str, subject: str, body: str, cc_emails=None):
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

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = to_email

    recipients = [to_email]

    # Handle CC emails
    if cc_emails:
        if isinstance(cc_emails, str):
            cc_emails = [cc_emails]

        msg["Cc"] = ", ".join(cc_emails)
        recipients.extend(cc_emails)

    with smtplib.SMTP(email_host, email_port) as server:
        server.starttls()
        server.login(email_user, email_password)
        server.sendmail(email_from, recipients, msg.as_string())