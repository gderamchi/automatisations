from __future__ import annotations

import argparse
import json
from typing import Any

from apps.workers.accounting.entries import generate_entries_for_document
from apps.workers.banking.importer import import_bank_csv
from apps.workers.banking.matching import match_bank_transactions
from apps.workers.common.database import init_db
from apps.workers.common.settings import get_settings
from apps.workers.documents.excel import write_document_to_excel
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.doe.service import rebuild_project_tree
from apps.workers.exports.inexweb import export_inexweb
from apps.workers.mail.worker import MailAutomationWorker
from apps.workers.sync.interfast import sync_interfast


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automation worker CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--source-path", required=True)
    ingest.add_argument("--source-kind", default="manual")
    ingest.add_argument("--source-name")

    ocr = subparsers.add_parser("run-ocr")
    ocr.add_argument("--document-id", type=int, required=True)

    excel = subparsers.add_parser("write-excel")
    excel.add_argument("--document-id", type=int, required=True)
    excel.add_argument("--mapping", default="purchases")

    accounting = subparsers.add_parser("generate-entries")
    accounting.add_argument("--document-id", type=int, required=True)

    bank = subparsers.add_parser("import-bank")
    bank.add_argument("--csv-path", required=True)
    bank.add_argument("--source-label")

    interfast = subparsers.add_parser("sync-interfast")
    interfast.add_argument("--force-full", action="store_true")
    interfast.add_argument("--entity-type", action="append", dest="entity_types")

    export = subparsers.add_parser("export-inexweb")
    export.add_argument("--output-path")

    doe = subparsers.add_parser("rebuild-doe")
    doe.add_argument("--project-id", type=int, required=True)

    mail = subparsers.add_parser("mail-worker")
    mail.add_argument("--once", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "init-db":
        init_db(settings)
        _print({"status": "ok", "db_path": str(settings.db_path)})
    elif args.command == "ingest":
        _print(ingest_document(args.source_path, args.source_kind, source_name=args.source_name, settings=settings))
    elif args.command == "run-ocr":
        _print(run_document_ocr(args.document_id, settings=settings))
    elif args.command == "write-excel":
        _print(write_document_to_excel(args.document_id, args.mapping, settings=settings))
    elif args.command == "generate-entries":
        _print(generate_entries_for_document(args.document_id, settings=settings))
    elif args.command == "import-bank":
        result = import_bank_csv(args.csv_path, source_label=args.source_label, settings=settings)
        result["matching"] = match_bank_transactions(settings=settings)
        _print(result)
    elif args.command == "sync-interfast":
        _print(sync_interfast(force_full=args.force_full, entity_types=args.entity_types, settings=settings))
    elif args.command == "export-inexweb":
        _print(export_inexweb(output_path=args.output_path, settings=settings))
    elif args.command == "rebuild-doe":
        _print(rebuild_project_tree(args.project_id, settings=settings))
    elif args.command == "mail-worker":
        worker = MailAutomationWorker(settings=settings)
        if args.once:
            _print(worker.run_once())
        else:
            worker.run_forever()
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
