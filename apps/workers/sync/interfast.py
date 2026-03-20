from __future__ import annotations

import json
from typing import Any

from apps.workers.common.database import get_connection, init_db, job_run
from apps.workers.common.jsonio import dump_json
from apps.workers.common.settings import Settings, get_settings
from apps.workers.connectors.interfast_client import InterfastClient
from apps.workers.doe.service import upsert_project


def _entity_since(connection, entity_type: str) -> str | None:
    row = connection.execute(
        """
        SELECT MAX(updated_at_remote) AS latest
        FROM interfast_entities
        WHERE entity_type = ?
        """,
        (entity_type,),
    ).fetchone()
    return row["latest"] if row and row["latest"] else None


def sync_interfast(
    force_full: bool = False,
    entity_types: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    client = InterfastClient(current)
    entity_types = entity_types or client.available_entities()
    summary: dict[str, Any] = {"entities": {}}

    with get_connection(current) as connection, job_run(connection, "interfast-sync"):
        for entity_type in entity_types:
            since = None if force_full else _entity_since(connection, entity_type)
            items = client.fetch_entities(entity_type, since=since, force_full=force_full)
            entity_config = client.config["entities"][entity_type]
            id_field = entity_config.get("id_field", "id")
            updated_field = entity_config.get("updated_field", "updated_at")

            for item in items:
                external_id = str(item[id_field])
                updated_at_remote = item.get(updated_field)
                connection.execute(
                    """
                    INSERT INTO interfast_entities(entity_type, external_id, updated_at_remote, payload_json, synced_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(entity_type, external_id)
                    DO UPDATE SET updated_at_remote = excluded.updated_at_remote, payload_json = excluded.payload_json, synced_at = CURRENT_TIMESTAMP
                    """,
                    (
                        entity_type,
                        external_id,
                        updated_at_remote,
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
                if entity_type == "projects":
                    upsert_project(
                        external_project_id=external_id,
                        project_code=item.get("code"),
                        project_name=item.get("name") or item.get("label") or f"Projet {external_id}",
                        metadata=item,
                        settings=current,
                    )

            dump_json(current.state_cache_dir / f"interfast_{entity_type}.json", items)
            summary["entities"][entity_type] = len(items)

        connection.commit()
    return summary
