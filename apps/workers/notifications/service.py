from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.settings import Settings, get_settings


def queue_notification(
    channel: str,
    recipient: str,
    body: str,
    subject: str | None = None,
    related_type: str | None = None,
    related_id: str | None = None,
    settings: Settings | None = None,
) -> int:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        cursor = connection.execute(
            """
            INSERT INTO notifications(channel, recipient, subject, body, related_type, related_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (channel, recipient, subject, body, related_type, related_id),
        )
        connection.commit()
    return int(cursor.lastrowid)


def send_telegram_message(body: str, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    if not current.telegram_bot_token or not current.telegram_chat_id:
        raise RuntimeError("Telegram settings are not configured")
    url = f"https://api.telegram.org/bot{current.telegram_bot_token}/sendMessage"
    response = httpx.post(url, json={"chat_id": current.telegram_chat_id, "text": body}, timeout=current.request_timeout_seconds)
    response.raise_for_status()
    return response.json()


def send_telegram_message_if_configured(body: str, settings: Settings | None = None) -> dict[str, Any] | None:
    current = settings or get_settings()
    if not current.telegram_bot_token or not current.telegram_chat_id:
        return None
    return send_telegram_message(body, settings=current)


def send_email(recipient: str, subject: str, body: str, settings: Settings | None = None) -> None:
    send_email_with_options(recipient=recipient, subject=subject, body=body, settings=settings)


def send_email_with_options(
    recipient: str,
    subject: str,
    body: str,
    *,
    settings: Settings | None = None,
    headers: dict[str, str] | None = None,
    reply_to: str | None = None,
) -> None:
    current = settings or get_settings()
    if not current.smtp_host:
        raise RuntimeError("SMTP_HOST is not configured")
    message = EmailMessage()
    message["From"] = current.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(body)

    with smtplib.SMTP(current.smtp_host, current.smtp_port) as smtp:
        smtp.starttls()
        if current.smtp_username and current.smtp_password:
            smtp.login(current.smtp_username, current.smtp_password)
        smtp.send_message(message)
