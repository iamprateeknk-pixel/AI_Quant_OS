"""Run trusted sample plugins on one bounded canonical CSV with the native engine."""

import argparse
import csv
import io
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import import_bars_csv_bytes
from ai_quant_os.native_tick_evidence import MAX_NATIVE_REPLAY_EVENTS
from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.strategy_registry import StrategyRegistry, builtin_catalog
from ai_quant_os.tick_data import import_ticks_csv_bytes


def _encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("Unsupported native result value")


def _validate_csv_row_limit(raw: bytes, max_rows: int) -> None:
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    for count, _ in enumerate(rows, 1):
        if count > max_rows:
            raise ValueError("CSV row limit exceeded")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Native Nautilus backtest on canonical bars or ticks"
    )
    parser.add_argument("dataset", type=Path, nargs="?")
    parser.add_argument("--format", choices=("bars", "ticks"), default="bars")
    parser.add_argument("--strategy", help="unique built-in strategy name from the catalog")
    parser.add_argument(
        "--strategy-ref", help="exact versioned strategy reference from the catalog"
    )
    parser.add_argument("--expected-catalog-sha256")
    parser.add_argument("--list-strategies", action="store_true")
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--fee", type=Decimal, default=Decimal(0))
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--fast-window", type=int)
    parser.add_argument("--slow-window", type=int)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        catalog_sha256, implementations = builtin_catalog()
        if args.list_strategies:
            if any(
                value is not None
                for value in (
                    args.dataset,
                    args.strategy,
                    args.strategy_ref,
                    args.expected_catalog_sha256,
                    args.output,
                    args.registry,
                    args.fast_window,
                    args.slow_window,
                )
            ) or (
                args.format != "bars"
                or args.slippage_bps != 0
                or args.fee != 0
                or args.quantity != 1
            ):
                raise ValueError("Catalog listing cannot be combined with a backtest request")
            inventory = {
                "schema_version": "1.0",
                "catalog_sha256": catalog_sha256,
                "strategies": [
                    {
                        "strategy_ref": row.strategy_ref,
                        "input_kind": row.input_kind,
                        "parameter_defaults": [list(pair) for pair in row.parameter_defaults],
                        "implementation_sha256": row.fingerprint,
                    }
                    for row in implementations
                ],
            }
            print(json.dumps(inventory, sort_keys=True, indent=2))
            return
        if args.dataset is None:
            raise ValueError("A dataset is required unless --list-strategies is used")
        if args.output is None:
            raise ValueError("--output is required for a backtest")
        if (
            args.expected_catalog_sha256 is not None
            and args.expected_catalog_sha256 != catalog_sha256
        ):
            raise ValueError("Built-in strategy catalog fingerprint changed")
        if args.strategy and args.strategy_ref:
            raise ValueError("Select either --strategy or --strategy-ref, not both")
        if args.strategy_ref:
            selected = next(
                (row for row in implementations if row.strategy_ref == args.strategy_ref), None
            )
            if selected is None:
                raise ValueError("Unknown versioned strategy reference")
        else:
            selected_name = args.strategy or (
                "tick-momentum" if args.format == "ticks" else "moving-average-cross"
            )
            matches = tuple(
                row for row in implementations if row.strategy_ref.split("@")[0] == selected_name
            )
            if not matches:
                raise ValueError("Unknown built-in strategy name")
            if len(matches) > 1:
                raise ValueError("Strategy name has multiple versions; use --strategy-ref")
            selected = matches[0]
        expected_kind = "TICK" if args.format == "ticks" else "BAR"
        if selected.input_kind != expected_kind:
            raise ValueError("Selected strategy is incompatible with the requested dataset format")
    except ImportError:
        parser.error("Built-in strategy catalog could not be read")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    try:
        from ai_quant_os.registered_backtest import run_registered_backtest
    except ImportError:
        parser.error('Install the trading extra: python -m pip install -e ".[trading]"')
    try:
        with args.dataset.open("rb") as source:
            raw = source.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            raise ValueError("Canonical CSV exceeds native runner byte limit")
        if args.format == "ticks":
            _validate_csv_row_limit(raw, MAX_NATIVE_REPLAY_EVENTS)
            dataset = import_ticks_csv_bytes(raw)
        else:
            _validate_csv_row_limit(raw, MAX_NATIVE_REPLAY_EVENTS // 2)
            dataset = import_bars_csv_bytes(raw)
        parameters = tuple(
            (key, value)
            for key, value in (("fast", args.fast_window), ("slow", args.slow_window))
            if value is not None
        )
        registered = run_registered_backtest(
            dataset,
            StrategyGenome(selected.strategy_ref, selected.input_kind, parameters),
            BacktestCosts(args.slippage_bps, args.fee),
            StrategyRegistry(args.registry or args.output.parent / ".strategy-registry"),
            quantity=args.quantity,
            expected_catalog_sha256=catalog_sha256,
        )
        report = {
            **asdict(registered.result),
            "source_sha256": sha256(raw).hexdigest(),
            "strategy_binding": asdict(registered.binding),
        }
        encoded = (
            json.dumps(report, default=_encode, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
        if args.output.resolve() == args.dataset.resolve():
            raise ValueError("Result output cannot overwrite its source dataset")
        args.output.write_text(encoded, encoding="utf-8")
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
