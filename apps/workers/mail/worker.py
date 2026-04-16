from __future__ import annotations

import imaplib
import json
import logging
import secrets
import smtplib
import time
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import compute_sha256, slugify
from apps.workers.common.settings import Settings, ensure_runtime_directories, get_settings
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import extract_document_insights, run_document_ocr
from apps.workers.routing.service import ensure_routing_task


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
    recipient_email: str
    body_text: str
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
        self.smtp_sender = smtp_sender
        self.sleep_fn = sleep_fn or time.sleep
        self.logger = _configure_logger(self.settings)

    def run_forever(self) -> None:
        self.logger.info("Starting mail worker with poll interval=%ss", self.settings.mail_poll_seconds)
        while True:
            try:
                summary = self.run_once()
                self.logger.info("Mail poll summary: %s", json.dumps(summary, ensure_ascii=False))
            except Exception:
                self.logger.exception("Mail poll failed, retrying next cycle")
            self.sleep_fn(self.settings.mail_poll_seconds)

    def run_once(self) -> dict[str, Any]:
        summary = {
            "messages_seen": 0,
            "messages_processed": 0,
            "attachments_processed": 0,
            "attachments_failed": 0,
            "routing_tasks_created": 0,
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

                processed_any = False
                attachment_results: list[dict[str, Any]] = []
                batch_token = secrets.token_urlsafe(18)
                for attachment in mail.attachments:
                    result = self._process_attachment(mail, attachment, batch_token=batch_token)
                    if result["status"] == "already_processed":
                        continue
                    processed_any = True
                    attachment_results.append(result)
                    if result["status"] == "error":
                        summary["attachments_failed"] += 1
                    else:
                        summary["attachments_processed"] += 1
                        summary["routing_tasks_created"] += int(bool(result.get("routing_task_created")))

                if attachment_results:
                    try:
                        self._send_reply(mail, attachment_results, batch_token)
                        summary["reply_sent"] += 1
                    except Exception:
                        self.logger.exception("Failed to send reply for uid=%s", uid)

                self._mark_seen(client, uid)
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
        recipient_email = _extract_first_address(
            message.get("Delivered-To", "")
            or message.get("X-Original-To", "")
            or message.get("To", "")
            or message.get("Cc", "")
        )
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
        body_text = _extract_body_text(message)

        return ParsedMail(
            uid=uid,
            message_id=message.get("Message-ID"),
            subject=subject,
            sender_email=sender_email,
            recipient_email=recipient_email,
            body_text=body_text,
            attachments=attachments,
            generated_reply=generated_reply,
        )

    def _process_attachment(self, mail: ParsedMail, attachment: MailAttachment, batch_token: str | None = None) -> dict[str, Any]:
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
                    "subject": mail.subject,
                    "body": mail.body_text,
                    "attachment_index": attachment.index,
                },
                settings=self.settings,
            )
            document_id = int(ingest_result["document_id"])
            if batch_token:
                with get_connection(self.settings) as conn:
                    conn.execute("UPDATE documents SET batch_token = ? WHERE id = ?", (batch_token, document_id))
                    conn.commit()
            has_payload = _document_has_payload(document_id, self.settings)
            if not ingest_result.get("duplicate") or not has_payload:
                ocr_result = run_document_ocr(document_id, settings=self.settings)
            else:
                ocr_result = {
                    "document_id": document_id,
                    "status": "validated",
                    "validation_required": False,
                }
                routing = ensure_routing_task(document_id, force_refresh=False, settings=self.settings)
            document_summary = _fetch_document_summary(document_id, self.settings)
            if ocr_result.get("validation_required") is False:
                routing = ensure_routing_task(document_id, force_refresh=False, settings=self.settings)
            else:
                routing = {"created": False}
            # For duplicates, fetch existing pending task tokens
            if ingest_result.get("duplicate"):
                pending = _fetch_pending_tokens(document_id, self.settings)
                if pending.get("validation_token"):
                    ocr_result["validation_token"] = pending["validation_token"]
                    ocr_result["validation_required"] = True
                if pending.get("routing_token"):
                    routing["routing_token"] = pending["routing_token"]
            interfast_link = _build_interfast_link(document_id, self.settings) if routing.get("auto_approved") else None
            result = {
                "status": "ok",
                "attachment": attachment.filename,
                "document_id": document_id,
                "ocr_status": ocr_result.get("status", document_summary.get("current_stage")),
                "validation_required": bool(ocr_result.get("validation_required", document_summary.get("validation_status") == "pending")),
                "validation_token": ocr_result.get("validation_token"),
                "routing_token": routing.get("routing_token"),
                "auto_approved": bool(routing.get("auto_approved")),
                "interfast_link": interfast_link,
                "confidence": document_summary.get("confidence"),
                "fields": document_summary.get("payload", {}),
                "duplicate": bool(ingest_result.get("duplicate")),
                "routing_task_created": bool(routing.get("created")) or (
                    not bool(ocr_result.get("validation_required", document_summary.get("validation_status") == "pending"))
                    and not bool(ingest_result.get("duplicate"))
                ),
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

    def _create_imap_client(self) -> imaplib.IMAP4_SSL:
        return imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)

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

    def _send_reply(self, mail: ParsedMail, results: list[dict[str, Any]], batch_token: str) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_username:
            self.logger.info("SMTP not configured, skipping reply")
            return
        base_url = self.settings.public_base_url.rstrip("/")
        reply_lines = [_format_attachment_result(r, base_url) for r in results]
        body = _build_reply_body(mail, reply_lines, base_url, batch_token)
        subject = f"{self.settings.mail_reply_subject_prefix} Re: {mail.subject or '(sans sujet)'}"
        reply_recipient = self.settings.reply_to_email or mail.recipient_email
        if not reply_recipient:
            self.logger.warning("No reply recipient found for uid=%s, skipping reply", mail.uid)
            return

        msg = MIMEMultipart()
        msg["From"] = self.settings.smtp_from or self.settings.smtp_username
        msg["To"] = reply_recipient
        msg["Subject"] = subject
        msg[REPLY_HEADER] = "1"
        if mail.message_id:
            msg["In-Reply-To"] = mail.message_id
            msg["References"] = mail.message_id
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if self.smtp_sender:
            self.smtp_sender(msg, reply_recipient)
        else:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
                server.starttls()
                server.login(self.settings.smtp_username, self.settings.smtp_password or "")
                server.send_message(msg)
        self.logger.info("Reply sent to %s for uid=%s", reply_recipient, mail.uid)

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


def _extract_body_text(message: EmailMessage) -> str:
    chunks: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        chunks.append(payload.decode(charset, errors="ignore"))
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def _extract_first_address(raw_value: str) -> str:
    if not raw_value:
        return ""
    for chunk in raw_value.split(","):
        address = parseaddr(chunk.strip())[1].lower()
        if address:
            return address
    return ""


def _is_supported_attachment(filename: str, content_type: str) -> bool:
    extension = Path(filename).suffix.lower()
    return extension in SUPPORTED_EXTENSIONS or content_type.startswith("image/") or content_type == "application/pdf"


INTERFAST_UI_PATHS = {
    "bill": "dashboard/billing/bills",
    "quotation": "dashboard/billing/quotations",
    "credit": "dashboard/billing/credits",
    "amendment": "dashboard/billing/amendments",
    "intervention": "dashboard/interventions",
    "expense": "dashboard/expenses?expenses=%7B%22pageIndex%22%3A0%7D",
}


def _build_interfast_link(document_id: int, settings: Settings) -> str | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT interfast_target_type, interfast_target_id FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    if not row or not row["interfast_target_type"] or not row["interfast_target_id"]:
        return None
    base = (settings.interfast_base_url or "https://app.inter-fast.fr").rstrip("/")
    target_type = row["interfast_target_type"]
    ui_path = INTERFAST_UI_PATHS.get(target_type)
    if not ui_path:
        return None
    if target_type == "expense":
        return f"{base}/{ui_path}"
    return f"{base}/{ui_path}/{row['interfast_target_id']}"


def _fetch_pending_tokens(document_id: int, settings: Settings) -> dict[str, str | None]:
    with get_connection(settings) as connection:
        vt = connection.execute(
            "SELECT token FROM validation_tasks WHERE document_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        rt = connection.execute(
            "SELECT token FROM routing_tasks WHERE document_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    return {
        "validation_token": vt["token"] if vt else None,
        "routing_token": rt["token"] if rt else None,
    }


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


def _build_reply_body(mail: ParsedMail, reply_lines: list[str], base_url: str, batch_token: str) -> str:
    needs_action = any("A VERIFIER" in line for line in reply_lines)
    header = "Des documents nécessitent votre validation" if needs_action else "Vos documents ont été traités avec succès"
    review_link = f"{base_url}/review/{batch_token}"
    action_block = (
        [
            "",
            "Vérifier et valider vos documents :",
            review_link,
        ]
        if needs_action
        else []
    )
    return "\n".join(
        [
            "Bonjour,",
            "",
            header,
            *action_block,
            "",
            f"Mail traité : {mail.subject or '(sans sujet)'}",
            f"Pièces jointes : {len(reply_lines)}",
            "",
            *reply_lines,
            "",
            "---",
            "Ceci est un message automatique.",
        ]
    )


def _format_attachment_result(result: dict[str, Any], base_url: str) -> str:
    if result["status"] == "error":
        return f"  - {result['attachment']} : ERREUR — {result['error']}"

    fields = result.get("fields", {})
    supplier = fields.get("supplier_name") or "Fournisseur inconnu"
    amount = fields.get("gross_amount") or "-"
    invoice_num = fields.get("invoice_number") or ""
    project = fields.get("project_ref") or ""

    summary = supplier
    if invoice_num:
        summary += f" n°{invoice_num}"
    if amount and amount != "-":
        summary += f" — {amount} EUR"
    if project:
        summary += f" (chantier : {project})"

    has_pending_task = result.get("validation_required") or result.get("routing_token")

    if result.get("duplicate") and not has_pending_task:
        return f"  - {result['attachment']} : déjà importé"

    validation_required = result.get("validation_required", False)
    auto_approved = result.get("auto_approved", False)
    validation_token = result.get("validation_token")
    validation_link = f"{base_url}/validate/{validation_token}" if validation_token else None
    routing_token = result.get("routing_token")
    routing_link = f"{base_url}/route/{routing_token}" if routing_token else None

    interfast_link = result.get("interfast_link")

    if auto_approved:
        line = f"  - {summary} → classé automatiquement"
        return f"{line}\n    Voir sur InterFast : {interfast_link}" if interfast_link else line
    elif validation_required:
        line = f"  - [A VERIFIER] {summary}"
        if validation_link:
            line += f"\n    Valider le document : {validation_link}"
        return line
    elif routing_token:
        line = f"  - [A VERIFIER] {summary} — chantier à confirmer"
        if routing_link:
            line += f"\n    Confirmer le chantier : {routing_link}"
        return line
    elif result.get("duplicate"):
        return f"  - {summary} → déjà classé"
    else:
        return f"  - {summary} → importé"


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
