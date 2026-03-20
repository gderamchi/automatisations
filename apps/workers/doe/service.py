from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import slugify
from apps.workers.common.settings import Settings, get_settings


STANDARD_SUBFOLDERS = [
    "01_administratif",
    "02_plans",
    "03_execution",
    "04_reception",
    "05_photos",
]


def upsert_project(
    external_project_id: str | None,
    project_code: str | None,
    project_name: str,
    metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> int:
    current = settings or get_settings()
    init_db(current)
    expected = json.dumps(current.doe_expected_documents)
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    with get_connection(current) as connection:
        connection.execute(
            """
            INSERT INTO doe_projects(external_project_id, project_code, project_name, expected_documents_json, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(external_project_id)
            DO UPDATE SET project_code = excluded.project_code, project_name = excluded.project_name, metadata_json = excluded.metadata_json, updated_at = CURRENT_TIMESTAMP
            """,
            (external_project_id, project_code, project_name, expected, metadata_json),
        )
        row = connection.execute(
            """
            SELECT id
            FROM doe_projects
            WHERE external_project_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (external_project_id,),
        ).fetchone()
        connection.commit()
    return int(row["id"])


def ensure_project_tree(project_id: int, settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    with get_connection(current) as connection:
        project = connection.execute(
            """
            SELECT id, project_code, project_name
            FROM doe_projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if not project:
            raise KeyError(f"DOE project not found: {project_id}")

        folder_name = slugify("_".join(filter(None, [project["project_code"], project["project_name"]])))
        base_path = current.doe_dir / folder_name
        base_path.mkdir(parents=True, exist_ok=True)
        for folder in STANDARD_SUBFOLDERS:
            (base_path / folder).mkdir(exist_ok=True)
        connection.execute(
            """
            UPDATE doe_projects
            SET base_path = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(base_path), project_id),
        )
        connection.commit()
    return base_path


def check_completeness(project_id: int, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    base_path = ensure_project_tree(project_id, current)
    with get_connection(current) as connection:
        project = connection.execute(
            """
            SELECT expected_documents_json
            FROM doe_projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        expected = json.loads(project["expected_documents_json"])
    existing = {path.stem.lower() for path in base_path.rglob("*") if path.is_file()}
    missing = [item for item in expected if not any(item in stem for stem in existing)]
    status = "complete" if not missing else "incomplete"
    with get_connection(current) as connection:
        connection.execute(
            """
            UPDATE doe_projects
            SET completeness_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, project_id),
        )
        connection.commit()
    return {
        "project_id": project_id,
        "base_path": str(base_path),
        "missing_documents": missing,
        "status": status,
    }


def generate_index_pdf(project_id: int, settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    base_path = ensure_project_tree(project_id, current)
    output_path = base_path / "index-doe.pdf"
    pdf = canvas.Canvas(str(output_path), pagesize=A4)
    pdf.setTitle("Index DOE")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, 800, f"Index DOE projet {project_id}")
    pdf.setFont("Helvetica", 10)
    y = 770
    for relative_file in sorted(path.relative_to(base_path) for path in base_path.rglob("*") if path.is_file() and path.name != output_path.name):
        pdf.drawString(50, y, str(relative_file))
        y -= 14
        if y < 50:
            pdf.showPage()
            pdf.setFont("Helvetica", 10)
            y = 800
    pdf.save()
    return output_path


def rebuild_project_tree(project_id: int, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    base_path = ensure_project_tree(project_id, current)
    completeness = check_completeness(project_id, current)
    index_pdf = generate_index_pdf(project_id, current)
    return {
        "project_id": project_id,
        "base_path": str(base_path),
        "index_pdf": str(index_pdf),
        "status": completeness["status"],
        "missing_documents": completeness["missing_documents"],
    }
