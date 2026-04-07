from __future__ import annotations

import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import compute_sha256, slugify
from apps.workers.common.settings import Settings, get_settings


def ingest_document(
    source_path: str,
    source_kind: str,
    source_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    metadata = metadata or {}
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    sha256 = compute_sha256(path)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    with get_connection(current) as connection:
        duplicate = connection.execute(
            """
            SELECT d.id, d.current_stage, df.stored_path
            FROM document_files df
            JOIN documents d ON d.id = df.document_id
            WHERE df.sha256 = ?
            """,
            (sha256,),
        ).fetchone()
        if duplicate:
            return {
                "document_id": duplicate["id"],
                "duplicate": True,
                "current_stage": duplicate["current_stage"],
                "stored_path": duplicate["stored_path"],
            }

        now = datetime.now(timezone.utc)
        target_dir = current.archive_originals_dir / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{now:%Y%m%dT%H%M%SZ}_{slugify(path.stem)[:48]}_{sha256[:12]}{path.suffix.lower()}"
        stored_path = target_dir / stored_name
        shutil.copy2(path, stored_path)

        document_cursor = connection.execute(
            """
            INSERT INTO documents(source_kind, source_name, source_subject, source_sender, source_body, archived_path, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_kind,
                source_name or path.name,
                metadata.get("subject"),
                metadata.get("sender_email") or metadata.get("from"),
                metadata.get("body"),
                str(stored_path),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        document_id = int(document_cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO document_files(document_id, original_name, stored_name, stored_path, sha256, mime_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (document_id, path.name, stored_name, str(stored_path), sha256, mime_type),
        )
        connection.commit()

    return {
        "document_id": document_id,
        "duplicate": False,
        "current_stage": "ingested",
        "stored_path": str(stored_path),
        "sha256": sha256,
    }
