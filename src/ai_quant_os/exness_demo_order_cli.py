"""Explicit operator entry point for the isolated Exness demo order gateway."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import SecretStr, ValidationError

from ai_quant_os.contracts import OrderCommand
from ai_quant_os.mt5_demo_execution import DemoOrderEnrollment
from ai_quant_os.mt5_demo_observation import DemoCaptureRequest
from ai_quant_os.mt5_demo_worker import (
    _order_command,
    supervise_demo_order,
    supervise_demo_reconciliation,
)

CONFIG_LIMIT = 16 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _read_json(path: Path):
    raw = path.read_bytes()
    if len(raw) > CONFIG_LIMIT:
        raise ValueError("Configuration exceeds the 16 KiB limit")
    return json.loads(raw, object_pairs_hook=_unique_object)


def load_enrollment(path: Path, environment: dict[str, str]) -> DemoOrderEnrollment:
    try:
        data = _read_json(path)
        if not isinstance(data, dict) or set(data) != {
            "connection",
            "enabled",
            "submission_ack",
            "max_order_lots",
        }:
            raise ValueError("Invalid enrollment shape")
        connection = data["connection"]
        if isinstance(connection, dict) and "password" in connection:
            raise ValueError("Password must come from AI_QUANT_MT5_PASSWORD environment variable")
        expected_connection = {
            "enabled",
            "connection_ack",
            "enrollment_id",
            "terminal_path",
            "sdk_version",
            "expected_login",
            "expected_server",
            "expected_company",
            "expected_currency",
            "symbols",
        }
        if not isinstance(connection, dict) or set(connection) != expected_connection:
            raise ValueError("invalid enrollment connection shape")
        password = environment.get("AI_QUANT_MT5_PASSWORD", "")
        if data["enabled"] and not password:
            raise ValueError(
                "Enabled enrollment requires AI_QUANT_MT5_PASSWORD environment variable"
            )
        request_fields = dict(connection)
        request_fields["expected_server"] = SecretStr(connection["expected_server"])
        request = DemoCaptureRequest(**request_fields, password=SecretStr(password or "disabled"))
        return DemoOrderEnrollment(
            connection=request, **{k: v for k, v in data.items() if k != "connection"}
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("Invalid enrollment configuration") from exc


def load_command(path: Path) -> OrderCommand:
    try:
        raw = _read_json(path)
        return _order_command(raw)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        KeyError,
        ValueError,
    ) as exc:
        raise ValueError("invalid command configuration") from exc


def main(
    argv=None, *, environment=None, runner=None, reconciliation_runner=None, clock=None
) -> int:
    parser = argparse.ArgumentParser(
        description="Submit or reconcile an explicitly admitted Exness demo order"
    )
    parser.add_argument("--enrollment", required=True, type=Path)
    parser.add_argument("--command", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--submit-demo-only", action="store_true")
    parser.add_argument("--reconcile-demo-only", action="store_true")
    args = parser.parse_args(argv)
    if args.submit_demo_only == args.reconcile_demo_only:
        print(
            "Choose exactly one of --submit-demo-only or --reconcile-demo-only",
            file=sys.stderr,
        )
        return 2
    environment = os.environ if environment is None else environment
    runner = supervise_demo_order if runner is None else runner
    reconciliation_runner = (
        supervise_demo_reconciliation if reconciliation_runner is None else reconciliation_runner
    )
    clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
    try:
        enrollment = load_enrollment(args.enrollment, environment)
        command = load_command(args.command)
        if not args.state_dir.is_absolute() or (
            os.name == "nt" and str(args.state_dir).startswith("\\\\")
        ):
            raise ValueError("State directory must be an absolute local path")
        state_dir = args.state_dir.resolve()
        state_dir.mkdir(parents=True, exist_ok=True)
        selected_runner = reconciliation_runner if args.reconcile_demo_only else runner
        result = selected_runner(
            enrollment, command, state_dir / "exness-demo-orders.sqlite3", clock=clock
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    serialized = result.model_dump_json()
    print(serialized)
    report = json.loads(serialized).get("report")
    state = report.get("state") if isinstance(report, dict) else None
    if state in {"ACKNOWLEDGED", "REJECTED"}:
        return 0
    return 1
