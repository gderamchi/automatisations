from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    data_root: Path = ROOT_DIR / "data"
    db_path: Path = ROOT_DIR / "data/state/sqlite/automation.db"
    internal_api_token: str = "change-me-internal-token"
    validation_username: str = "validator"
    validation_password: str = "change-me-validation-password"
    ocr_confidence_threshold: float = 0.82
    ocr_mock_mode: bool = False
    mistral_api_key: str | None = None
    mistral_ocr_model: str = "mistral-ocr-latest"
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_mailbox: str = "INBOX"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "noreply@example.local"
    reply_to_email: str | None = None
    mail_poll_seconds: int = 30
    mail_reply_subject_prefix: str = "[AUTOMATISATIONS OCR]"
    mark_processed_seen: bool = True
    mail_bootstrap_current_uid: bool = True
    interfast_base_url: str | None = None
    interfast_api_key: str | None = None
    interfast_timeout_seconds: int = 30
    interfast_write_mode: str = "disabled"
    interfast_attachment_field_name: str = "file"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    export_delimiter: str = ";"
    export_encoding: str = "utf-8-sig"
    bank_match_certain_threshold: float = 0.92
    bank_match_probable_threshold: float = 0.70
    routing_match_threshold: float = 0.72
    routing_match_gap: float = 0.10
    routing_auto_approve_threshold: float = 0.85
    routing_auto_dispatch: bool = True
    dashboard_refresh_seconds: int = 30
    app_timezone: str = "Europe/Paris"
    request_timeout_seconds: int = 30
    app_name: str = "automatisations-platform"
    public_base_url: str | None = None
    default_excel_mapping: str = "purchases"
    default_excel_mappings: list[str] = Field(
        default_factory=lambda: [
            "grand_livre",
            "tresorerie",
            "chantiers",
            "tva",
        ]
    )
    optional_excel_mappings: list[str] = Field(
        default_factory=lambda: [
            "client_grand_livre",
        ]
    )
    weekly_accounting_recipient: str | None = None
    weekly_accounting_subject_prefix: str = "COMPTABILITÉ"
    doe_expected_documents: list[str] = Field(
        default_factory=lambda: [
            "devis",
            "commande",
            "facture",
            "pv_reception",
        ]
    )

    @property
    def config_dir(self) -> Path:
        return ROOT_DIR / "config"

    @property
    def contracts_dir(self) -> Path:
        return self.config_dir / "contracts"

    @property
    def templates_dir(self) -> Path:
        return self.config_dir / "templates"

    @property
    def rules_dir(self) -> Path:
        return self.config_dir / "rules"

    @property
    def excel_mappings_dir(self) -> Path:
        return self.config_dir / "excel_mappings"

    @property
    def incoming_email_dir(self) -> Path:
        return self.data_root / "incoming" / "email"

    @property
    def incoming_manual_dir(self) -> Path:
        return self.data_root / "incoming" / "manual"

    @property
    def processing_dir(self) -> Path:
        return self.data_root / "processing"

    @property
    def archive_originals_dir(self) -> Path:
        return self.data_root / "archive" / "originals"

    @property
    def archive_normalized_dir(self) -> Path:
        return self.data_root / "archive" / "normalized"

    @property
    def exports_inexweb_dir(self) -> Path:
        return self.data_root / "exports" / "inexweb"

    @property
    def doe_dir(self) -> Path:
        return self.data_root / "doe"

    @property
    def state_sqlite_dir(self) -> Path:
        return self.data_root / "state" / "sqlite"

    @property
    def state_cache_dir(self) -> Path:
        return self.data_root / "state" / "cache"

    @property
    def state_logs_dir(self) -> Path:
        return self.data_root / "state" / "logs"

    @property
    def docs_runtime_dir(self) -> Path:
        return self.data_root / "docs"

    @property
    def classified_standard_dir(self) -> Path:
        return self.data_root / "classified" / "standard"

    @property
    def classified_accounting_dir(self) -> Path:
        return self.data_root / "classified" / "accounting"

    @property
    def classified_worksites_dir(self) -> Path:
        return self.data_root / "classified" / "worksites"

    @property
    def all_managed_directories(self) -> list[Path]:
        return [
            self.incoming_email_dir,
            self.incoming_manual_dir,
            self.processing_dir,
            self.archive_originals_dir,
            self.archive_normalized_dir,
            self.exports_inexweb_dir,
            self.doe_dir,
            self.state_sqlite_dir,
            self.state_cache_dir,
            self.state_logs_dir,
            self.docs_runtime_dir,
            self.classified_standard_dir,
            self.classified_accounting_dir,
            self.classified_worksites_dir,
        ]

    @model_validator(mode="after")
    def validate_public_base_url(self) -> "Settings":
        environment = self.environment.lower().strip()
        configured_url = (self.public_base_url or "").strip()
        if not configured_url:
            if environment == "development":
                configured_url = f"http://127.0.0.1:{self.api_port}"
            else:
                raise ValueError("PUBLIC_BASE_URL is required outside development")

        parsed = urlparse(configured_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_BASE_URL must be an absolute http(s) URL")

        if environment != "development" and parsed.scheme != "https":
            raise ValueError("PUBLIC_BASE_URL must use https outside development")

        host = (parsed.hostname or "").lower()
        if environment != "development" and host in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("PUBLIC_BASE_URL cannot point to localhost outside development")

        normalized = configured_url.rstrip("/")
        object.__setattr__(self, "public_base_url", normalized)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def ensure_runtime_directories(settings: Settings | None = None) -> None:
    current = settings or get_settings()
    for path in current.all_managed_directories:
        path.mkdir(parents=True, exist_ok=True)
    current.db_path.parent.mkdir(parents=True, exist_ok=True)
