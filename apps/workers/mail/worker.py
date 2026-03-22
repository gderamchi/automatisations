from __future__ import annotations

import imaplib
import json
import logging
import time
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import compute_sha256, slugify
from apps.workers.common.settings import Settings, ensure_runtime_directories, get_settings
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import extract_document_insights, run_document_ocr
from apps.workers.notifications.service import send_email_with_options


LOGGER_NAME = "automatisations.mail_worker"
REPLY_HEADER = "X-Automatisations-Reply"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".txt"}


@dataclass
class MailAttachment:
    index: int
    filename: str
    content_type: str
    payload: bytes
    supported: bool


@dataclass
class ParsedMail:
    uid: str
    message_id: str | None
    subject: str
    sender_email: str
    attachments: list[MailAttachment]
    generated_reply: bool


class MailAutomationWorker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        imap_factory: Callable[[], imaplib.IMAP4_SSL] | None = None,
        smtp_sender: Callable[..., None] | None = None,
        sleep_fn: Callable[[int], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        ensure_runtime_directories(self.settings)
        init_db(self.settings)
        self.imap_factory = imap_factory or self._create_imap_client
        self.smtp_sender = smtp_sender or self._send_reply_email
        self.sleep_fn = sleep_fn or time.sleep
        self.logger = _configure_logger(self.settings)

    def run_forever(self) -> None:
        self.logger.info("Starting mail worker with poll interval=%ss", self.settings.mail_poll_seconds)
        while True:
            summary = self.run_once()
            self.logger.info("Mail poll summary: %s", json.dumps(summary, ensure_ascii=False))
            self.sleep_fn(self.settings.mail_poll_seconds)

    def run_once(self) -> dict[str, Any]:
        summary = {
            "messages_seen": 0,
            "messages_processed": 0,
            "attachments_processed": 0,
            "attachments_failed": 0,
            "reply_sent": 0,
            "bootstrapped": False,
        }
        client = self.imap_factory()
        try:
            self._login_and_select(client)
            last_uid = self._get_last_uid()
            if last_uid is None and self.settings.mail_bootstrap_current_uid:
                current_uid = self._fetch_current_max_uid(client)
                self._set_last_uid(current_uid)
                summary["bootstrapped"] = True
                return summary

            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            raw_uids = data[0].split() if data and data[0] else []
            raw_uids = [uid for uid in raw_uids if int(uid) > (last_uid or 0)]
            summary["messages_seen"] = len(raw_uids)
            max_processed_uid = last_uid or 0
            for uid_bytes in raw_uids:
                uid = uid_bytes.decode()
                max_processed_uid = max(max_processed_uid, int(uid))
                mail = self._fetch_mail(client, uid)
                if mail.generated_reply:
                    self.logger.info("Ignoring generated reply uid=%s subject=%s", uid, mail.subject)
                    self._mark_seen(client, uid)
                    continue

                if not mail.attachments:
                    self.logger.info("Ignoring email without attachments uid=%s subject=%s", uid, mail.subject)
                    self._mark_seen(client, uid)
                    continue

                reply_lines: list[str] = []
                processed_any = False
                for attachment in mail.attachments:
                    result = self._process_attachment(mail, attachment)
                    if result["status"] == "already_processed":
                        continue
                    processed_any = True
                    if result["status"] == "error":
                        summary["attachments_failed"] += 1
                    else:
                        summary["attachments_processed"] += 1
                    reply_lines.append(_format_attachment_result(result))

                self._mark_seen(client, uid)
                if processed_any and reply_lines:
                    self.smtp_sender(
                        recipient=self._reply_recipient(),
                        subject=f"{self.settings.mail_reply_subject_prefix} {mail.subject or '(sans sujet)'}",
                        body=_build_reply_body(mail, reply_lines),
                    )
                    summary["reply_sent"] += 1
                summary["messages_processed"] += 1
            if max_processed_uid:
                self._set_last_uid(max_processed_uid)
            return summary
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    def _login_and_select(self, client: imaplib.IMAP4_SSL) -> None:
        if not self.settings.imap_username or not self.settings.imap_password:
            raise RuntimeError("IMAP credentials are not configured")
        client.login(self.settings.imap_username, self.settings.imap_password)
        status, _ = client.select(self.settings.imap_mailbox)
        if status != "OK":
            raise RuntimeError(f"Unable to select mailbox {self.settings.imap_mailbox}")

    def _fetch_mail(self, client: imaplib.IMAP4_SSL, uid: str) -> ParsedMail:
        status, data = client.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            raise RuntimeError(f"Unable to fetch uid={uid}")
        raw_message = b""
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                raw_message = bytes(item[1])
                break
        if not raw_message:
            raise RuntimeError(f"IMAP returned no payload for uid={uid}")

        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        sender_email = parseaddr(message.get("From", ""))[1].lower()
        subject = str(message.get("Subject", "") or "")
        generated_reply = (
            message.get(REPLY_HEADER, "") == "1"
            or subject.startswith(self.settings.mail_reply_subject_prefix)
        )

        attachments = [
            MailAttachment(
                index=index,
                filename=filename,
                content_type=part.get_content_type(),
                payload=payload,
                supported=_is_supported_attachment(filename, part.get_content_type()),
            )
            for index, (filename, payload, part) in enumerate(_iter_attachments(message), start=1)
        ]

        return ParsedMail(
            uid=uid,
            message_id=message.get("Message-ID"),
            subject=subject,
            sender_email=sender_email,
            attachments=attachments,
            generated_reply=generated_reply,
        )

    def _process_attachment(self, mail: ParsedMail, attachment: MailAttachment) -> dict[str, Any]:
        incoming_path = _persist_attachment(self.settings, mail.uid, attachment)
        attachment_sha256 = compute_sha256(incoming_path)
        processed_key = f"{mail.uid}:{attachment.index}:{attachment_sha256}"
        if self._processed_key_exists(processed_key):
            return {"status": "already_processed", "attachment": attachment.filename}

        result: dict[str, Any]
        document_id: int | None = None
        try:
            if not attachment.supported:
                result = {
                    "status": "error",
                    "attachment": attachment.filename,
                    "error": f"Unsupported attachment type: {attachment.content_type}",
                }
                self._record_processed(
                    processed_key=processed_key,
                    mail=mail,
                    attachment=attachment,
                    attachment_sha256=attachment_sha256,
                    document_id=None,
                    status="error",
                    payload=result,
                    error_text=result["error"],
                )
                return result
            ingest_result = ingest_document(
                str(incoming_path),
                "email",
                source_name=mail.subject or attachment.filename,
                metadata={
                    "mailbox_uid": mail.uid,
                    "message_id": mail.message_id,
                    "sender_email": mail.sender_email,
                    "attachment_index": attachment.index,
                },
                settings=self.settings,
            )
            document_id = int(ingest_result["document_id"])
            has_payload = _document_has_payload(document_id, self.settings)
            if not ingest_result.get("duplicate") or not has_payload:
                ocr_result = run_document_ocr(document_id, settings=self.settings)
            else:
                ocr_result = {
                    "document_id": document_id,
                    "status": "validated",
                    "validation_required": False,
                }
            document_summary = _fetch_document_summary(document_id, self.settings)
            result = {
                "status": "ok",
                "attachment": attachment.filename,
                "document_id": document_id,
                "ocr_status": ocr_result.get("status", document_summary.get("current_stage")),
                "validation_required": bool(ocr_result.get("validation_required", document_summary.get("validation_status") == "pending")),
                "confidence": document_summary.get("confidence"),
                "fields": document_summary.get("payload", {}),
                "duplicate": bool(ingest_result.get("duplicate")),
            }
            self._record_processed(
                processed_key=processed_key,
                mail=mail,
                attachment=attachment,
                attachment_sha256=attachment_sha256,
                document_id=document_id,
                status="ok",
                payload=result,
            )
            return result
        except Exception as exc:
            result = {
                "status": "error",
                "attachment": attachment.filename,
                "error": str(exc),
            }
            self._record_processed(
                processed_key=processed_key,
                mail=mail,
                attachment=attachment,
                attachment_sha256=attachment_sha256,
                document_id=document_id,
                status="error",
                payload=result,
                error_text=str(exc),
            )
            self.logger.exception("Attachment processing failed uid=%s attachment=%s", mail.uid, attachment.filename)
            return result

    def _reply_recipient(self) -> str:
        recipient = self.settings.reply_to_email or self.settings.imap_username or self.settings.smtp_from
        if not recipient:
            raise RuntimeError("REPLY_TO_EMAIL or IMAP/SMTP identity must be configured")
        return recipient

    def _create_imap_client(self) -> imaplib.IMAP4_SSL:
        return imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)

    def _send_reply_email(self, *, recipient: str, subject: str, body: str) -> None:
        send_email_with_options(
            recipient=recipient,
            subject=subject,
            body=body,
            settings=self.settings,
            headers={REPLY_HEADER: "1"},
        )

    def _processed_key_exists(self, processed_key: str) -> bool:
        with get_connection(self.settings) as connection:
            row = connection.execute(
                "SELECT 1 FROM processed_emails WHERE processed_key = ?",
                (processed_key,),
            ).fetchone()
        return row is not None

    def _get_last_uid(self) -> int | None:
        with get_connection(self.settings) as connection:
            row = connection.execute(
                "SELECT state_value FROM worker_state WHERE state_key = 'mail_last_uid'"
            ).fetchone()
        return int(row["state_value"]) if row else None

    def _set_last_uid(self, value: int) -> None:
        with get_connection(self.settings) as connection:
            connection.execute(
                """
                INSERT INTO worker_state(state_key, state_value, updated_at)
                VALUES ('mail_last_uid', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(state_key)
                DO UPDATE SET state_value = excluded.state_value, updated_at = CURRENT_TIMESTAMP
                """,
                (str(value),),
            )
            connection.commit()

    def _record_processed(
        self,
        *,
        processed_key: str,
        mail: ParsedMail,
        attachment: MailAttachment,
        attachment_sha256: str,
        document_id: int | None,
        status: str,
        payload: dict[str, Any],
        error_text: str | None = None,
    ) -> None:
        with get_connection(self.settings) as connection:
            connection.execute(
                """
                INSERT INTO processed_emails(
                    processed_key, mailbox_uid, message_id, sender, subject,
                    attachment_index, attachment_filename, attachment_sha256,
                    document_id, status, result_json, error_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    processed_key,
                    mail.uid,
                    mail.message_id,
                    mail.sender_email,
                    mail.subject,
                    attachment.index,
                    attachment.filename,
                    attachment_sha256,
                    document_id,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    error_text,
                ),
            )
            connection.commit()

    def _mark_seen(self, client: imaplib.IMAP4_SSL, uid: str) -> None:
        if not self.settings.mark_processed_seen:
            return
        client.uid("store", uid, "+FLAGS", "(\\Seen)")

    def _fetch_current_max_uid(self, client: imaplib.IMAP4_SSL) -> int:
        status, data = client.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return 0
        return max(int(uid) for uid in data[0].split())


def _persist_attachment(settings: Settings, uid: str, attachment: MailAttachment) -> Path:
    extension = Path(attachment.filename).suffix.lower()
    stem = slugify(Path(attachment.filename).stem)
    filename = f"{uid}_{attachment.index:02d}_{stem[:48]}{extension}"
    target = settings.incoming_email_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(attachment.payload)
    return target


def _iter_attachments(message: EmailMessage):
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if not filename and disposition != "attachment":
            continue
        filename = filename or f"attachment-{slugify(part.get_content_type())}"
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        yield filename, payload, part


def _is_supported_attachment(filename: str, content_type: str) -> bool:
    extension = Path(filename).suffix.lower()
    return extension in SUPPORTED_EXTENSIONS or content_type.startswith("image/") or content_type == "application/pdf"


def _document_has_payload(document_id: int, settings: Settings) -> bool:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT normalized_payload_json
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    return bool(row and row["normalized_payload_json"])


def _fetch_document_summary(document_id: int, settings: Settings) -> dict[str, Any]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT supplier_name, supplier_siret, invoice_number, invoice_date, due_date, net_amount,
                   vat_amount, gross_amount, project_ref, confidence, validation_status, current_stage,
                   validated_payload_json, normalized_payload_json
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Document not found: {document_id}")
    payload_json = row["validated_payload_json"] or row["normalized_payload_json"]
    payload = json.loads(payload_json) if payload_json else {}
    raw_text = payload.get("raw_text") or ""
    return {
        "payload": payload,
        "confidence": row["confidence"],
        "validation_status": row["validation_status"],
        "current_stage": row["current_stage"],
        "insights": extract_document_insights(raw_text),
        "raw_text_excerpt": _build_raw_excerpt(raw_text),
    }


def _build_raw_excerpt(raw_text: str, max_length: int = 900) -> str:
    compact = "\n".join(line.strip() for line in raw_text.splitlines() if line.strip())
    return compact[:max_length]


def _build_reply_body(mail: ParsedMail, reply_lines: list[str]) -> str:
    return "\n".join(
        [
            "Résultat du traitement automatique",
            "",
            f"Sujet source: {mail.subject or '(sans sujet)'}",
            f"Expéditeur source: {mail.sender_email or '(inconnu)'}",
            f"Pièces jointes traitées: {len(reply_lines)}",
            "",
            *reply_lines,
        ]
    )


def _format_attachment_result(result: dict[str, Any]) -> str:
    if result["status"] == "error":
        return "\n".join(
            [
                f"- Fichier: {result['attachment']}",
                "  Statut: ERREUR",
                f"  Détail: {result['error']}",
            ]
        )

    fields = result.get("fields", {})
    insights = result.get("insights", {})
    raw_excerpt = result.get("raw_text_excerpt")
    analysis_lines = []
    if insights.get("payment_status"):
        analysis_lines.append(f"  Statut paiement détecté: {insights['payment_status']}")
    if insights.get("payment_reference"):
        analysis_lines.append(f"  Référence paiement: {insights['payment_reference']}")
    if insights.get("order_number"):
        analysis_lines.append(f"  Numéro de commande: {insights['order_number']}")
    if insights.get("seller_name"):
        analysis_lines.append(f"  Vendu par: {insights['seller_name']}")
    if insights.get("issuer_name") and insights.get("issuer_name") != fields.get("supplier_name"):
        analysis_lines.append(f"  Émetteur détecté: {insights['issuer_name']}")

    return "\n".join(
        [
            f"- Fichier: {result['attachment']}",
            f"  Statut: {'DUPLICATE' if result.get('duplicate') else 'OK'} / {result.get('ocr_status', 'unknown')}",
            f"  Validation manuelle requise: {'oui' if result.get('validation_required') else 'non'}",
            f"  Confiance OCR: {result.get('confidence')}",
            f"  Fournisseur: {fields.get('supplier_name') or '-'}",
            f"  Numéro: {fields.get('invoice_number') or '-'}",
            f"  Date: {fields.get('invoice_date') or '-'}",
            f"  Montant TTC: {fields.get('gross_amount') or '-'}",
            f"  Réf chantier: {fields.get('project_ref') or '-'}",
            *analysis_lines,
            "  Extrait OCR:",
            *[f"    {line}" for line in (raw_excerpt.splitlines()[:8] if raw_excerpt else ["-"])],
        ]
    )


def _configure_logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_path = settings.state_logs_dir / "mail_worker.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
