"""Run trusted sample plugins on one bounded canonical CSV with the native engine."""

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import import_bars_csv_bytes
from ai_quant_os.strategy_samples import SAMPLE_STRATEGIES
from ai_quant_os.tick_data import import_ticks_csv_bytes
from ai_quant_os.tick_strategy import TickMomentum


def _encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("Unsupported native result value")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Native Nautilus backtest on canonical bars or ticks"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--format", choices=("bars", "ticks"), default="bars")
    parser.add_argument("--strategy", choices=(*SAMPLE_STRATEGIES, "tick-momentum"))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--fee", type=Decimal, default=Decimal(0))
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from ai_quant_os.nautilus_runtime import run_nautilus_backtest, run_nautilus_tick_backtest
    except ImportError:
        parser.error('Install the trading extra: python -m pip install -e ".[trading]"')
    try:
        with args.dataset.open("rb") as source:
            raw = source.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            raise ValueError("Canonical CSV exceeds native runner byte limit")
        name = args.strategy or (
            "tick-momentum" if args.format == "ticks" else "moving-average-cross"
        )
        if args.format == "ticks":
            if name != "tick-momentum":
                raise ValueError("Tick format requires a tick strategy")
            dataset = import_ticks_csv_bytes(raw)
            runner, plugin = run_nautilus_tick_backtest, TickMomentum()
        else:
            if name not in SAMPLE_STRATEGIES:
                raise ValueError("Bar format requires a bar strategy")
            dataset = import_bars_csv_bytes(raw)
            runner, plugin = run_nautilus_backtest, SAMPLE_STRATEGIES[name]()
        result = runner(
            dataset,
            plugin,
            BacktestCosts(args.slippage_bps, args.fee),
            quantity=args.quantity,
            strategy_ref=name + "@1.0.0",
        )
        report = {**asdict(result), "source_sha256": sha256(raw).hexdigest()}
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
