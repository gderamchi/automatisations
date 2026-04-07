from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from apps.workers.common.jsonio import load_json
from apps.workers.common.settings import Settings, get_settings
from apps.workers.common.templating import substitute_env


class InterfastClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        candidates = [
            self.settings.rules_dir / "interfast_endpoints.json",
            self.settings.rules_dir / "interfast_endpoints.example.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return load_json(candidate)
        return {"entities": {}}

    def available_entities(self) -> list[str]:
        return sorted(self.config.get("entities", {}).keys())

    def fetch_entities(self, entity_type: str, since: str | None = None, force_full: bool = False) -> list[dict[str, Any]]:
        if self.settings.ocr_mock_mode and not self.settings.interfast_base_url:
            return []

        entity_config = self.config.get("entities", {}).get(entity_type)
        if not entity_config:
            raise KeyError(f"Unknown Interfast entity type: {entity_type}")
        if not self.settings.interfast_base_url:
            raise RuntimeError("INTERFAST_BASE_URL is required for real syncs")

        url = urljoin(f"{self.settings.interfast_base_url.rstrip('/')}/", entity_config["path"].lstrip("/"))
        headers = {key: substitute_env(value) for key, value in self.config.get("headers", {}).items()}
        if self.settings.interfast_api_key:
            headers.setdefault("X-API-KEY", self.settings.interfast_api_key)

        params = dict(entity_config.get("params", {}))
        updated_after_param = entity_config.get("updated_after_param")
        if since and updated_after_param and not force_full:
            params[updated_after_param] = since

        list_field = entity_config.get("list_field")
        all_items: list[dict[str, Any]] = []

        with httpx.Client(timeout=self.settings.interfast_timeout_seconds) as client:
            while True:
                response = client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()

                if list_field:
                    items = payload.get(list_field, [])
                elif isinstance(payload, list):
                    items = payload
                else:
                    items = payload.get("items", [])
                if not isinstance(items, list):
                    raise RuntimeError(f"Unexpected payload for entity {entity_type}: expected a list")
                all_items.extend(items)

                pageable = payload.get("pageable", {}) if isinstance(payload, dict) else {}
                total = payload.get("count", 0) if isinstance(payload, dict) else 0
                if not pageable or not items or len(all_items) >= total:
                    break
                params["page"] = str(int(params.get("page", "1")) + 1)

        return all_items
