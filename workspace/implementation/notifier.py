# workspace/implementation/notifier.py
import logging
import os
import smtplib
import subprocess
from email.message import EmailMessage
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def notify_desktop(message: str, *, _subprocess_run: Optional[Callable] = None) -> None:
    """Send a macOS desktop notification via osascript."""
    runner = _subprocess_run if _subprocess_run is not None else subprocess.run
    script = f'display notification "{message}" with title "Bay Club Court Checker"'
    try:
        runner(["osascript", "-e", script], check=True, capture_output=True)
        logger.info("Desktop notification sent.")
    except FileNotFoundError:
        print(f"[MATCH FOUND] {message}")
    except subprocess.CalledProcessError as exc:
        logger.warning("osascript returned non-zero exit code %d", exc.returncode)
        print(f"[MATCH FOUND] {message}")


def notify_email(message: str, *, _smtp_factory: Optional[Callable] = None) -> None:
    """Send an email notification via SMTP if env vars are configured."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    notify_addr = os.environ.get("NOTIFY_EMAIL")
    if not all([smtp_host, smtp_port, smtp_user, smtp_password, notify_addr]):
        return
    factory = _smtp_factory if _smtp_factory is not None else smtplib.SMTP
    msg = EmailMessage()
    msg["Subject"] = "Bay Club: Court slot available!"
    msg["From"] = smtp_user
    msg["To"] = notify_addr
    msg.set_content(message)
    with factory(smtp_host, int(smtp_port)) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)
    logger.info("Email notification sent to %s", notify_addr)
