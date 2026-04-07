from __future__ import annotations

import zipfile
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import smtplib

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.settings import Settings, get_settings
from apps.workers.notifications.service import queue_notification, send_telegram_message_if_configured


def _previous_iso_week(reference_date: date) -> tuple[int, int]:
    previous_week_date = reference_date - timedelta(days=7)
    iso_year, iso_week, _ = previous_week_date.isocalendar()
    return iso_year, iso_week


def _collect_weekly_documents(iso_year: int, iso_week: int, settings: Settings) -> list[tuple[int, Path]]:
    paths: list[tuple[int, Path]] = []
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, archived_path, final_filename, created_at
            FROM documents
            WHERE validation_status = 'approved'
            """
        ).fetchall()
    for row in rows:
        created_at = str(row["created_at"] or "")
        try:
            created_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(ZoneInfo(settings.app_timezone)).date()
        except ValueError:
            continue
        current_year, current_week, _ = created_date.isocalendar()
        if current_year != iso_year or current_week != iso_week:
            continue
        preferred = settings.classified_accounting_dir / str(row["final_filename"] or "")
        archived = Path(row["archived_path"])
        if preferred.exists():
            paths.append((int(row["id"]), preferred))
        elif archived.exists():
            paths.append((int(row["id"]), archived))
    return paths


def build_weekly_accounting_zip(
    reference_date: date | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    current = settings or get_settings()
    init_db(current)
    local_today = reference_date or datetime.now(ZoneInfo(current.app_timezone)).date()
    iso_year, iso_week = _previous_iso_week(local_today)
    documents = _collect_weekly_documents(iso_year, iso_week, current)
    zip_name = f"COMPTA_SEMAINE_{iso_week:02d}_{iso_year}.zip"
    zip_path = current.exports_inexweb_dir / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for document_id, path in documents:
            archive.write(path, arcname=path.name)
    return {
        "zip_path": str(zip_path),
        "document_ids": [document_id for document_id, _ in documents],
        "iso_week": iso_week,
        "iso_year": iso_year,
    }


def send_weekly_accounting_email(
    reference_date: date | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    current = settings or get_settings()
    if not current.weekly_accounting_recipient:
        raise RuntimeError("WEEKLY_ACCOUNTING_RECIPIENT is not configured")
    if not current.smtp_host:
        raise RuntimeError("SMTP_HOST is not configured")
    bundle = build_weekly_accounting_zip(reference_date=reference_date, settings=current)
    zip_path = Path(str(bundle["zip_path"]))
    subject = f"{current.weekly_accounting_subject_prefix} - SEMAINE {bundle['iso_week']:02d} - CCM"
    body = (
        "Bonjour,\n"
        f"Veuillez trouver ci-joint les documents comptables de la semaine {bundle['iso_week']:02d}-{bundle['iso_year']}.\n"
        "Cordialement"
    )

    message = EmailMessage()
    message["From"] = current.smtp_from
    message["To"] = current.weekly_accounting_recipient
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(zip_path.read_bytes(), maintype="application", subtype="zip", filename=zip_path.name)

    with smtplib.SMTP(current.smtp_host, current.smtp_port) as smtp:
        smtp.starttls()
        if current.smtp_username and current.smtp_password:
            smtp.login(current.smtp_username, current.smtp_password)
        smtp.send_message(message)

    queue_notification(
        channel="telegram",
        recipient=current.telegram_chat_id or "telegram",
        body=f"Dossier {zip_path.name}, a été envoyé au destinataire comptable.",
        related_type="weekly-accounting",
        related_id=zip_path.name,
        settings=current,
    )
    send_telegram_message_if_configured(
        f"Dossier {zip_path.name}, a été envoyé au destinataire comptable.",
        settings=current,
    )
    return bundle
