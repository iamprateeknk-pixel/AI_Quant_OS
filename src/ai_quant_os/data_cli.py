import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.tick_archive import TickArchive
from ai_quant_os.rate_cli import RATE_COMMANDS, configure_rate_commands, execute_rate_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-quant-data")
    commands = parser.add_subparsers(dest="command", required=True)
    configure_rate_commands(commands)
    for suffix in ("", "-ticks"):
        ingest = commands.add_parser(
            f"ingest{suffix}", help="Archive an immutable CSV source and Parquet dataset"
        )
        ingest.add_argument("source", type=Path)
        ingest.add_argument("--root", type=Path, required=True)
        ingest.add_argument("--source-id", required=True)
        ingest.add_argument("--at", help="Ingestion time as an aware ISO 8601 timestamp")
        ingest.add_argument("--catalog", action="store_true", help="Save lineage to PostgreSQL")
        replay = commands.add_parser(f"replay{suffix}", help="Verify and replay an archive")
        replay.add_argument("archive_id")
        replay.add_argument("--root", type=Path, required=True)
        health = commands.add_parser(f"health{suffix}", help="Report archive freshness")
        health.add_argument("archive_id")
        health.add_argument("--root", type=Path, required=True)
        health.add_argument("--now", help="Current time as an aware ISO 8601 timestamp")
        health.add_argument("--max-age-seconds", type=float, required=True)
        health.add_argument("--catalog", action="store_true", help="Save quality to PostgreSQL")
        health.add_argument("--trace-id", help="Trace identifier for a saved quality observation")
        publish = commands.add_parser(
            f"publish-clickhouse{suffix}", help="Publish a verified archive to ClickHouse"
        )
        publish.add_argument("archive_id")
        publish.add_argument("--root", type=Path, required=True)
        publish.add_argument("--endpoint-url", help="ClickHouse HTTP endpoint")
    for name in ("mirror", "restore"):
        remote = commands.add_parser(name, help=f"{name.title()} an archive through S3")
        remote.add_argument("archive_id")
        remote.add_argument("--kind", choices=("bars", "ticks"), required=True)
        remote.add_argument("--root", type=Path, required=True)
        remote.add_argument("--bucket", required=True)
        remote.add_argument("--prefix", default="")
        remote.add_argument("--endpoint-url", help="S3-compatible service URL")
    cache = commands.add_parser("cache-ticks", help="Cache latest verified ticks in Valkey")
    cache.add_argument("archive_id")
    cache.add_argument("--root", type=Path, required=True)
    cache.add_argument("--ttl-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command in RATE_COMMANDS:
        return execute_rate_command(args)
    if args.command == "cache-ticks":
        import redis

        from ai_quant_os.valkey_cache import LatestTickCache

        valkey_url = os.getenv("AI_QUANT_VALKEY_URL")
        if not valkey_url:
            parser.error("Set AI_QUANT_VALKEY_URL to use cache-ticks")
        client = redis.Redis.from_url(
            valkey_url, socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False
        )
        updated = LatestTickCache(client).publish(
            TickArchive(args.root), args.archive_id, ttl_seconds=args.ttl_seconds
        )
        print(json.dumps({"archive_id": args.archive_id, "updated": updated}, sort_keys=True))
        return 0
    if args.command in {"mirror", "restore"}:
        import boto3

        from ai_quant_os.s3_archive import S3ArchiveMirror

        client = boto3.client("s3", endpoint_url=args.endpoint_url)
        mirror = S3ArchiveMirror(client, bucket=args.bucket, prefix=args.prefix)
        if args.command == "mirror":
            archive = TickArchive(args.root) if args.kind == "ticks" else BarArchive(args.root)
            manifest = (
                mirror.publish_tick(archive, args.archive_id)
                if args.kind == "ticks"
                else mirror.publish_bar(archive, args.archive_id)
            )
        else:
            manifest = (
                mirror.restore_tick(args.archive_id, args.root)
                if args.kind == "ticks"
                else mirror.restore_bar(args.archive_id, args.root)
            )
        print(json.dumps(asdict(manifest), default=lambda value: value.isoformat(), sort_keys=True))
        return 0
    if args.command in {"publish-clickhouse", "publish-clickhouse-ticks"}:
        from ai_quant_os.clickhouse_publication import ClickHouseArchivePublisher

        endpoint = args.endpoint_url or os.getenv("AI_QUANT_CLICKHOUSE_URL")
        user = os.getenv("AI_QUANT_CLICKHOUSE_USER")
        password = os.getenv("AI_QUANT_CLICKHOUSE_PASSWORD")
        if not endpoint or not user or not password:
            parser.error(
                "Set AI_QUANT_CLICKHOUSE_URL, AI_QUANT_CLICKHOUSE_USER, and "
                "AI_QUANT_CLICKHOUSE_PASSWORD"
            )
        publisher = ClickHouseArchivePublisher(endpoint, user=user, password=password)
        is_tick = args.command.endswith("-ticks")
        result = (
            publisher.publish_ticks(TickArchive(args.root), args.archive_id)
            if is_tick
            else publisher.publish_bars(BarArchive(args.root), args.archive_id)
        )
        print(json.dumps(asdict(result), sort_keys=True))
        return 0
    is_tick = args.command.endswith("-ticks")
    archive = TickArchive(args.root) if is_tick else BarArchive(args.root)
    use_catalog = getattr(args, "catalog", False)
    database_url = os.getenv("AI_QUANT_DATA_DATABASE_URL") if use_catalog else None
    if use_catalog and not database_url:
        parser.error("Set AI_QUANT_DATA_DATABASE_URL to use --catalog")
    if args.command.startswith("ingest"):
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        manifest = archive.ingest_csv(args.source, source_id=args.source_id, ingested_at=at)
        if database_url:
            from ai_quant_os.lineage_catalog import PostgresLineageCatalog

            catalog = PostgresLineageCatalog(database_url)
            catalog.register_source(args.source_id, kind="CSV")
            if is_tick:
                catalog.record_tick_archive(manifest)
            else:
                catalog.record_archive(manifest)
        print(json.dumps(asdict(manifest), default=lambda value: value.isoformat(), sort_keys=True))
        return 0
    if args.command.startswith("replay"):
        dataset = archive.replay(args.archive_id)
        print(
            json.dumps(
                {
                    "archive_id": args.archive_id,
                    "dataset_fingerprint": dataset.fingerprint,
                    "row_count": len(dataset.ticks if is_tick else dataset.bars),
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

        catalog = PostgresLineageCatalog(database_url)
        if is_tick:
            catalog.record_tick_quality(
                args.archive_id, report, checked_at=now, trace_id=args.trace_id
            )
        else:
            catalog.record_quality(args.archive_id, report, checked_at=now, trace_id=args.trace_id)
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.fresh else 2


if __name__ == "__main__":
    raise SystemExit(main())
