"""Measure repeated native replay on a deterministic synthetic development dataset."""

import argparse
import json
import math
import platform
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.nautilus_runtime import run_nautilus_backtest
from ai_quant_os.strategy_samples import MovingAverageCrossover


def main():
    parser = argparse.ArgumentParser(
        description="Record local Nautilus development replay baseline"
    )
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.bars <= 10000 or not 2 <= args.iterations <= 10:
        parser.error("Use 3..10000 bars and 2..10 iterations")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dataset = BarDataset(
        Bar(
            "EURUSD",
            start + timedelta(seconds=i),
            *([Decimal("1.1") + Decimal("0.00001") * (i % 20)] * 4),
            Decimal(100),
        )
        for i in range(args.bars)
    )
    baseline, timings = None, []
    for _ in range(args.iterations):
        begin = perf_counter()
        result = run_nautilus_backtest(
            dataset,
            MovingAverageCrossover(),
            BacktestCosts(Decimal(1), Decimal("0.01")),
            strategy_ref="moving-average-cross@1.0.0",
        )
        timings.append((perf_counter() - begin) * 1000)
        if baseline is not None and result != baseline:
            raise ValueError("Native replay is not reproducible")
        baseline = result
    ordered = sorted(timings)
    report = {
        "schema_version": "1.0",
        "engine_version": baseline.engine_version,
        "provenance": "SYNTHETIC_DEVELOPMENT_BASELINE",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "bar_count": args.bars,
        "dataset_fingerprint": dataset.fingerprint,
        "iterations": args.iterations,
        "reproducible": True,
        "native_order_count": baseline.native_order_count,
        "elapsed_ms": timings,
        "median_ms": median(timings),
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
