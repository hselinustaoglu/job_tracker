"""Email notifications for daily job alerts."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .models import JobPosting


class EmailConfigError(RuntimeError):
    """Raised when SMTP configuration is incomplete."""


def maybe_send_email_alert(
    new_jobs: list[JobPosting],
    removed_jobs: list[JobPosting],
    summary_text: str,
) -> None:
    _load_dotenv()
    config = _load_email_config()
    if not new_jobs and not removed_jobs:
        subject = "job_tracker: no changes today"
    else:
        subject = f"job_tracker: {len(new_jobs)} new, {len(removed_jobs)} removed"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from_email"]
    message["To"] = config["to_email"]
    message.set_content(summary_text)

    with smtplib.SMTP(config["host"], int(config["port"])) as smtp:
        if config["starttls"]:
            smtp.starttls()
        if config["username"]:
            smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def _load_email_config() -> dict[str, str | bool]:
    required = {
        "SMTP_HOST": os.getenv("SMTP_HOST"),
        "SMTP_PORT": os.getenv("SMTP_PORT"),
        "ALERT_FROM_EMAIL": os.getenv("ALERT_FROM_EMAIL"),
        "ALERT_TO_EMAIL": os.getenv("ALERT_TO_EMAIL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise EmailConfigError(f"missing env vars: {', '.join(missing)}")

    return {
        "host": required["SMTP_HOST"],
        "port": required["SMTP_PORT"],
        "from_email": required["ALERT_FROM_EMAIL"],
        "to_email": required["ALERT_TO_EMAIL"],
        "username": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "starttls": os.getenv("SMTP_STARTTLS", "true").casefold() != "false",
    }


def _load_dotenv(dotenv_path: str = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
