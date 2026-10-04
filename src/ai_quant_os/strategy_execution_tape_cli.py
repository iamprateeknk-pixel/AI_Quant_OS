"""Explicit pinned receipt assembly or native numeric replay; execute no proposed source."""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_execution_tape import (
    assemble_strategy_execution_tape,
    replay_strategy_execution_tape,
)
from ai_quant_os.strategy_execution_tape_contracts import StrategyExecutionTape
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_execution_contracts import StrategyPrefixExecutionReceipt
from ai_quant_os.validation_package_store import ValidationPackageStore

MAX_METADATA = 134217728


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate completion metadata field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite completion metadata")


def _pinned(path, digest):
    require_digest(digest, prefix="")
    raw = read_repair_image_input(path, MAX_METADATA)
    if sha256(raw).hexdigest() != digest:
        raise ValueError("Independent completion metadata hash mismatch")
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect pinned completions or replay targets")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("assemble", "replay"):
        sub = commands.add_parser(command)
        sub.add_argument("--archive", required=True, type=Path)
        sub.add_argument("--package-ref", required=True)
        sub.add_argument("--image-id", required=True)
        sub.add_argument("--worker-sha256", required=True)
        sub.add_argument("--output", required=True, type=Path)
        name = "receipts" if command == "assemble" else "collection"
        sub.add_argument("--" + name, required=True, type=Path)
        sub.add_argument("--" + name + "-sha256", required=True)
        if command == "replay":
            sub.add_argument("--quantity", type=int, default=1)
            sub.add_argument("--initial-balance", type=Decimal, default=Decimal("100000"))
            sub.add_argument("--cost-scenario", choices=("BASE", "DECLARED_STRESS"), default="BASE")
    args = parser.parse_args()
    try:
        require_digest(args.image_id)
        require_digest(args.worker_sha256, prefix="")
        if not args.archive.is_file() or args.output.exists():
            raise ValueError("Existing package archive and unused output required")
        with ValidationPackageStore(args.archive) as store:
            package = store.get(args.package_ref)
        if package is None:
            raise ValueError("Original package not found")
        if args.command == "assemble":
            rows = _pinned(args.receipts, args.receipts_sha256)
            if type(rows) is not list or len(rows) > 4094:
                raise ValueError("Bounded completion inventory array required")
            value = assemble_strategy_execution_tape(
                package,
                tuple(StrategyPrefixExecutionReceipt.model_validate(row) for row in rows),
                image_id=args.image_id,
                worker_sha256=args.worker_sha256,
            )
            reference = value.collection_ref
            status = "UNATTESTED_COMPLETION_TAPE"
        else:
            collection = StrategyExecutionTape.model_validate(
                _pinned(args.collection, args.collection_sha256)
            )
            if (collection.image_id, collection.worker_sha256) != (
                args.image_id,
                args.worker_sha256,
            ):
                raise ValueError("Independent completion image/source pins changed")
            value = replay_strategy_execution_tape(
                package,
                collection,
                quantity=args.quantity,
                initial_balance=args.initial_balance,
                cost_scenario=args.cost_scenario,
            )
            reference, status = value.report_ref, value.status
        raw = value.model_dump_json().encode()
        if len(raw) > MAX_METADATA:
            raise ValueError("Completion output exceeds bound")
        with args.output.open("xb") as destination:
            destination.write(raw)
            destination.flush()
            os.fsync(destination.fileno())
        print(json.dumps({"status": status, "reference": reference}, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error, UnicodeError, RecursionError):
        parser.exit(
            2,
            "Completion bridge refused; check original package, independent pins, bounded metadata and unused output.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
