from __future__ import annotations

from email.message import EmailMessage

from apps.workers.common.database import get_connection
from apps.workers.mail.worker import MailAutomationWorker


class FakeImapClient:
    def __init__(self, messages: dict[str, bytes]):
        self.messages = {uid: {"raw": raw, "seen": False} for uid, raw in messages.items()}

    def login(self, _username, _password):
        return "OK", [b"logged-in"]

    def select(self, _mailbox):
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        normalized = command.lower()
        if normalized == "search":
            unseen = [uid.encode() for uid, payload in self.messages.items() if not payload["seen"]]
            return "OK", [b" ".join(unseen)]
        if normalized == "fetch":
            uid = _normalize_uid(args[0])
            raw = self.messages[uid]["raw"]
            return "OK", [(b"1 (RFC822 {0})", raw)]
        if normalized == "store":
            uid = _normalize_uid(args[0])
            self.messages[uid]["seen"] = True
            return "OK", [b""]
        raise AssertionError(f"Unsupported IMAP command: {command}")

    def close(self):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


def _normalize_uid(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def _build_message(*, subject: str, sender: str, attachments: list[tuple[str, bytes, str, str]], headers: dict[str, str] | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "inbox@example.com"
    message["Subject"] = subject
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content("Bonjour")
    for filename, payload, maintype, subtype in attachments:
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def test_mail_worker_processes_multiple_attachments_and_creates_routing_tasks(test_settings, sample_invoice_text):
    sent_messages = []
    fake_imap = FakeImapClient(
        {
            "101": _build_message(
                subject="Factures chantier",
                sender="supplier@example.com",
                attachments=[
                    ("invoice-a.txt", sample_invoice_text.encode("utf-8"), "text", "plain"),
                    ("invoice-b.txt", sample_invoice_text.encode("utf-8"), "text", "plain"),
                ],
            )
        }
    )

    worker = MailAutomationWorker(
        settings=test_settings,
        imap_factory=lambda: fake_imap,
        smtp_sender=lambda *args, **kwargs: sent_messages.append((args, kwargs)),
    )

    summary = worker.run_once()

    assert summary["messages_seen"] == 1
    assert summary["attachments_processed"] == 2
    assert summary["routing_tasks_created"] == 1
    assert summary["reply_sent"] == 1
    assert len(sent_messages) == 1
    assert sent_messages[0][0][1] == "owner@example.com"

    with get_connection(test_settings) as connection:
        processed_count = connection.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
        routing_count = connection.execute("SELECT COUNT(*) FROM routing_tasks").fetchone()[0]
    assert processed_count == 2
    assert routing_count == 1


def test_mail_worker_does_not_reprocess_same_email(test_settings, sample_invoice_text):
    raw_message = _build_message(
        subject="Facture unique",
        sender="supplier@example.com",
        attachments=[("invoice.txt", sample_invoice_text.encode("utf-8"), "text", "plain")],
    )
    first_imap = FakeImapClient({"201": raw_message})
    second_imap = FakeImapClient({"201": raw_message})
    sent_messages = []

    first_worker = MailAutomationWorker(
        settings=test_settings,
        imap_factory=lambda: first_imap,
        smtp_sender=lambda *args, **kwargs: sent_messages.append((args, kwargs)),
    )
    second_worker = MailAutomationWorker(
        settings=test_settings,
        imap_factory=lambda: second_imap,
        smtp_sender=lambda *args, **kwargs: sent_messages.append((args, kwargs)),
    )

    first_summary = first_worker.run_once()
    second_summary = second_worker.run_once()

    assert first_summary["routing_tasks_created"] == 1
    assert first_summary["reply_sent"] == 1
    assert second_summary["reply_sent"] == 0
    assert second_summary["routing_tasks_created"] == 0
    assert len(sent_messages) == 1
    assert sent_messages[0][0][1] == "owner@example.com"


def test_mail_worker_ignores_generated_reply_and_reports_unsupported_attachment(test_settings):
    sent_messages = []
    fake_imap = FakeImapClient(
        {
            "301": _build_message(
                subject="[AUTOMATISATIONS OCR] Réponse automatique",
                sender="inbox@example.com",
                attachments=[("ignored.txt", b"ignored", "text", "plain")],
                headers={"X-Automatisations-Reply": "1"},
            ),
            "302": _build_message(
                subject="Document docx",
                sender="supplier@example.com",
                attachments=[
                    (
                        "unsupported.docx",
                        b"fake-docx",
                        "application",
                        "vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                ],
            ),
        }
    )

    worker = MailAutomationWorker(
        settings=test_settings,
        imap_factory=lambda: fake_imap,
        smtp_sender=lambda *args, **kwargs: sent_messages.append((args, kwargs)),
    )

    summary = worker.run_once()

    assert summary["messages_seen"] == 2
    assert summary["attachments_failed"] == 1
    assert summary["reply_sent"] == 1
    assert summary["routing_tasks_created"] == 0
    assert len(sent_messages) == 1
    assert sent_messages[0][0][1] == "owner@example.com"
