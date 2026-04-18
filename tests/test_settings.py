from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.workers.common.settings import Settings


def test_public_base_url_required_outside_development(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_public_base_url_rejects_loopback_outside_development(base_url):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            public_base_url=base_url,
        )


def test_public_base_url_normalizes_trailing_slash():
    settings = Settings(
        _env_file=None,
        environment="production",
        public_base_url="https://ocr.example.com/",
    )

    assert settings.public_base_url == "https://ocr.example.com"


def test_public_base_url_requires_https_outside_development():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            public_base_url="http://ocr.example.com",
        )


def test_public_base_url_defaults_to_local_in_development(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)

    settings = Settings(_env_file=None, environment="development", api_port=8080)

    assert settings.public_base_url == "http://127.0.0.1:8080"
