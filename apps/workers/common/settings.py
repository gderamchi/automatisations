from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    interfast_base_url: str | None = None
    interfast_api_key: str | None = None
    interfast_timeout_seconds: int = 30
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "noreply@example.local"
    export_delimiter: str = ";"
    export_encoding: str = "utf-8-sig"
    bank_match_certain_threshold: float = 0.92
    bank_match_probable_threshold: float = 0.70
    dashboard_refresh_seconds: int = 30
    app_timezone: str = "Europe/Paris"
    request_timeout_seconds: int = 30
    app_name: str = "automatisations-platform"
    default_excel_mapping: str = "purchases"
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
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def ensure_runtime_directories(settings: Settings | None = None) -> None:
    current = settings or get_settings()
    for path in current.all_managed_directories:
        path.mkdir(parents=True, exist_ok=True)
    current.db_path.parent.mkdir(parents=True, exist_ok=True)
