from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from mistralai import Mistral  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - SDK layout differs by build/distribution.
    from mistralai.client import Mistral

from apps.workers.common.settings import Settings, get_settings


class MistralOCRClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY is required when OCR_MOCK_MODE=false")
        self.client = Mistral(api_key=self.settings.mistral_api_key)

    def process_file(self, file_path: Path) -> dict[str, Any]:
        with file_path.open("rb") as handle:
            uploaded = self.client.files.upload(
                file={"file_name": file_path.name, "content": handle},
                purpose="ocr",
            )

        response = self.client.ocr.process(
            model=self.settings.mistral_ocr_model,
            document={"type": "file", "file_id": uploaded.id},
        )
        return response.model_dump(mode="json")
