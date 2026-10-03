"""Explicit rate capture and verified archival read commands."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from ai_quant_os.ecb_rates import EcbRateClient
from ai_quant_os.policy_rates import MAX_RATE_CSV_BYTES, normalize_ecb_rates
from ai_quant_os.rate_archive import RateArchive

RATE_COMMANDS = frozenset({"fetch-ecb-rates", "ingest-ecb-rates", "replay-rates", "list-rates"})


def configure_rate_commands(commands: argparse._SubParsersAction) -> None:
    fetch = commands.add_parser(
        "fetch-ecb-rates", help="Explicitly retrieve three public ECB policy-rate series"
    )
    fetch.add_argument("--root", type=Path, required=True)
    ingest = commands.add_parser(
        "ingest-ecb-rates",
        help="Import a bounded local ECB-format CSV with disclosed local provenance",
    )
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--root", type=Path, required=True)
    ingest.add_argument("--at", help="Local ingestion receipt time as an aware ISO timestamp")
    replay = commands.add_parser(
        "replay-rates", help="Reconstruct and verify one immutable rate capture"
    )
    replay.add_argument("capture_ref")
    replay.add_argument("--root", type=Path, required=True)
    listing = commands.add_parser("list-rates", help="List verified rate capture identities")
    listing.add_argument("--root", type=Path, required=True)


def _source(path: Path) -> bytes:
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Rate CSV must be a regular file")
        raw = stream.read(MAX_RATE_CSV_BYTES + 1)
    if not 0 < len(raw) <= MAX_RATE_CSV_BYTES:
        raise ValueError("Rate CSV exceeds byte budget")
    return raw


def execute_rate_command(args: argparse.Namespace) -> int:
    try:
        path = args.root / "rates.sqlite3"
        if args.command in {"fetch-ecb-rates", "ingest-ecb-rates"}:
            if args.command == "fetch-ecb-rates":
                retrieved = asyncio.run(EcbRateClient().fetch())
                raw, at, mode = retrieved.raw, retrieved.received_at, "ECB_HTTPS_RETRIEVAL"
            else:
                raw = _source(args.source)
                at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
                mode = "LOCAL_CSV_IMPORT"
            normalize_ecb_rates(raw, received_at=at, capture_mode=mode)
            args.root.mkdir(parents=True, exist_ok=True)
            with RateArchive(path) as archive:
                capture = archive.publish(raw, received_at=at, capture_mode=mode)
            result = {
                "capture_ref": capture.capture_ref,
                "capture": capture.model_dump(mode="json"),
            }
        else:
            if not path.is_file():
                raise ValueError("Rate archive missing")
            with RateArchive(path) as archive:
                if args.command == "list-rates":
                    result = [
                        {
                            "capture_ref": row.capture_ref,
                            "received_at": row.received_at.isoformat(),
                            "raw_sha256": row.raw_sha256,
                            "capture_mode": row.capture_mode,
                        }
                        for row in archive.list()
                    ]
                else:
                    capture = archive.get(args.capture_ref)
                    if capture is None:
                        raise ValueError("Rate capture not found")
                    result = {
                        "capture_ref": capture.capture_ref,
                        "capture": capture.model_dump(mode="json"),
                    }
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error):
        print(
            "Rate data operation refused; verify source, storage and archive integrity.",
            file=sys.stderr,
        )
        return 2
