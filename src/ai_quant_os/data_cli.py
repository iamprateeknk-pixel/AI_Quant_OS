import argparse
import json
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
    replay = commands.add_parser("replay", help="Verify and replay an archived dataset")
    replay.add_argument("archive_id")
    replay.add_argument("--root", type=Path, required=True)
    health = commands.add_parser("health", help="Report bar freshness for an archive")
    health.add_argument("archive_id")
    health.add_argument("--root", type=Path, required=True)
    health.add_argument("--now", help="Current time as an aware ISO 8601 timestamp")
    health.add_argument("--max-age-seconds", type=float, required=True)
    args = parser.parse_args(argv)
    archive = BarArchive(args.root)
    if args.command == "ingest":
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        manifest = archive.ingest_csv(args.source, source_id=args.source_id, ingested_at=at)
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
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.fresh else 2


if __name__ == "__main__":
    raise SystemExit(main())
