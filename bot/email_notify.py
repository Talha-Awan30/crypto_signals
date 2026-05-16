"""SMTP email sender — same Gmail-friendly path as before."""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import RUNTIME

log = logging.getLogger(__name__)


def send_email(title: str, body: str) -> bool:
    if not RUNTIME.smtp_user or not RUNTIME.smtp_password or not RUNTIME.email_to:
        log.error("email not configured (SMTP_USER/PASSWORD/EMAIL_TO missing)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"] = RUNTIME.email_from or RUNTIME.smtp_user
    msg["To"] = RUNTIME.email_to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    html = "<pre style='font-family:monospace;font-size:13px'>" + body.replace("<", "&lt;") + "</pre>"
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(RUNTIME.smtp_host, RUNTIME.smtp_port, timeout=20) as s:
            s.starttls()
            s.login(RUNTIME.smtp_user, RUNTIME.smtp_password)
            s.sendmail(msg["From"], [RUNTIME.email_to], msg.as_string())
        log.info("email sent: %s", title)
        return True
    except Exception as e:
        log.error("email failed: %s", e)
        return False
