import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quant_os.data_archive import BarArchive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-quant-data")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "ingest", help="Archive an immutable CSV source and Parquet dataset"
    )
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--root", type=Path, required=True)
    ingest.add_argument("--source-id", required=True)
    ingest.add_argument("--at", help="Ingestion time as an aware ISO 8601 timestamp")
    ingest.add_argument("--catalog", action="store_true", help="Save lineage to PostgreSQL")
    replay = commands.add_parser("replay", help="Verify and replay an archived dataset")
    replay.add_argument("archive_id")
    replay.add_argument("--root", type=Path, required=True)
    health = commands.add_parser("health", help="Report bar freshness for an archive")
    health.add_argument("archive_id")
    health.add_argument("--root", type=Path, required=True)
    health.add_argument("--now", help="Current time as an aware ISO 8601 timestamp")
    health.add_argument("--max-age-seconds", type=float, required=True)
    health.add_argument("--catalog", action="store_true", help="Save quality to PostgreSQL")
    health.add_argument("--trace-id", help="Trace identifier for a saved quality observation")
    args = parser.parse_args(argv)
    archive = BarArchive(args.root)
    use_catalog = getattr(args, "catalog", False)
    database_url = os.getenv("AI_QUANT_DATA_DATABASE_URL") if use_catalog else None
    if use_catalog and not database_url:
        parser.error("Set AI_QUANT_DATA_DATABASE_URL to use --catalog")
    if args.command == "ingest":
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        manifest = archive.ingest_csv(args.source, source_id=args.source_id, ingested_at=at)
        if database_url:
            from ai_quant_os.lineage_catalog import PostgresLineageCatalog

            catalog = PostgresLineageCatalog(database_url)
            catalog.register_source(args.source_id, kind="CSV")
            catalog.record_archive(manifest)
        print(json.dumps(asdict(manifest), default=lambda value: value.isoformat(), sort_keys=True))
        return 0
    if args.command == "replay":
        dataset = archive.replay(args.archive_id)
        print(
            json.dumps(
                {
                    "archive_id": args.archive_id,
                    "dataset_fingerprint": dataset.fingerprint,
                    "row_count": len(dataset.bars),
                },
                sort_keys=True,
            )
        )
        return 0
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    report = archive.health(
        args.archive_id, now=now, max_age=timedelta(seconds=args.max_age_seconds)
    )
    if database_url:
        if not args.trace_id:
            parser.error("--trace-id is required with --catalog for health")
        from ai_quant_os.lineage_catalog import PostgresLineageCatalog

        PostgresLineageCatalog(database_url).record_quality(
            args.archive_id, report, checked_at=now, trace_id=args.trace_id
        )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.fresh else 2


if __name__ == "__main__":
    raise SystemExit(main())
