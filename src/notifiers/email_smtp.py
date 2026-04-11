"""SMTP email notifier (Gmail by default)."""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import CONFIG
from .base import Notifier

log = logging.getLogger(__name__)


class EmailNotifier(Notifier):
    def send(self, title: str, body: str) -> bool:
        if not CONFIG.smtp_user or not CONFIG.smtp_password or not CONFIG.email_to:
            log.error("Email notifier not configured (SMTP_USER/PASSWORD/EMAIL_TO missing)")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = CONFIG.email_from or CONFIG.smtp_user
        msg["To"] = CONFIG.email_to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        html_body = "<pre style='font-family:monospace;font-size:13px'>" + body.replace("<", "&lt;") + "</pre>"
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(CONFIG.smtp_host, CONFIG.smtp_port, timeout=20) as s:
                s.starttls()
                s.login(CONFIG.smtp_user, CONFIG.smtp_password)
                s.sendmail(msg["From"], [CONFIG.email_to], msg.as_string())
            log.info("email sent: %s", title)
            return True
        except Exception as e:
            log.error("email send failed: %s", e)
            return False
