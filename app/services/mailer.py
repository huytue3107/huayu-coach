"""Outgoing e-mail. With SMTP configured it sends; otherwise the message is written to data/outbox/ (dev/pilot)."""
from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from ..config import get_settings

log = logging.getLogger("huayu.mail")


def outbox_tag(address: str) -> str:
    return hashlib.sha256(address.lower().encode()).hexdigest()[:10]


def purge_outbox(address: str) -> int:
    """Remove the files the no-SMTP outbox wrote for this address (used when the account is erased)."""
    folder = get_settings().data_dir / "outbox"
    removed = 0
    if folder.is_dir():
        for path in folder.glob(f"*-{outbox_tag(address)}.txt"):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def send_mail(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if re.search(r"[\r\n]", to + subject):
        raise ValueError("header injection")
    if not s.smtp_host:
        outbox = s.data_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        # the recipient is inside the file, not in its name or in the log line (no addresses in logs)
        name = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{outbox_tag(to)}.txt"
        (outbox / name).write_text(f"To: {to}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
        log.info("mail written to the outbox (%s)", name)
        return True
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from or s.smtp_user or "noreply@localhost", to, subject
    msg.set_content(body)
    try:
        context = ssl.create_default_context()  # verifies the server certificate and host name
        if s.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=context)
        else:
            smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with smtp:
            if s.smtp_security == "starttls":
                smtp.starttls(context=context)
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value() if s.smtp_password else "")
            smtp.send_message(msg)
        return True
    except Exception:  # noqa: BLE001
        log.exception("smtp send failed")
        return False
