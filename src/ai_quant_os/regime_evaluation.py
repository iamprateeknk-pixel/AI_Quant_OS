"""Replay the actual A02 graph against a bounded, explicitly synthetic baseline."""

import argparse
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_context_sources import paper_context_sources
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.regime_agent import regime_implementation
from ai_quant_os.world_state import WorldStateEngine


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=64)
    start: Decimal = Field(gt=0, allow_inf_nan=False)
    step: Decimal | None = Field(default=None, allow_inf_nan=False)
    alternate: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def mode(self) -> "Segment":
        if (self.step is None) == (self.alternate is None):
            raise ValueError("Exactly one fixture segment mode required")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    segments: tuple[Segment, ...] = Field(min_length=1, max_length=64)
    expected: tuple[str, str, str]

    @model_validator(mode="after")
    def bounds(self) -> "Case":
        if not 3 <= sum(row.count for row in self.segments) <= 64 or not set(
            self.expected
        ).issubset({"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}):
            raise ValueError("Invalid fixture case bounds")
        return self


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT"]
    cases: tuple[Case, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def identity(self) -> "Fixture":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate fixture cases")
        return self


async def evaluate_regime_fixture(path: Path) -> dict:
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Regime fixture exceeds limit")
    fixture = Fixture.model_validate_json(raw)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry = load_agent_catalog()
    results = []
    with TemporaryDirectory(prefix="ai-quant-regime-eval-") as directory:
        async with GraphRuntime.open(
            Path(directory) / "graph.sqlite3",
            registry=registry,
            implementations={("A02", "0.2.0"): regime_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: now,
        ) as runtime:
            for case in fixture.cases:
                closes = tuple(
                    value
                    for segment in case.segments
                    for value in (
                        (segment.start if i % 2 == 0 else segment.alternate)
                        if segment.alternate is not None
                        else segment.start + segment.step * i
                        for i in range(segment.count)
                    )
                )
                bars = tuple(
                    Bar(
                        "EURUSD",
                        now - timedelta(seconds=len(closes) - i - 1),
                        close,
                        close,
                        close,
                        close,
                        Decimal(1),
                    )
                    for i, close in enumerate(closes)
                )
                engine = WorldStateEngine(PAPER_INSTRUMENTS)
                engine.record_bars(bars, now=now)
                snapshot = engine.snapshot(
                    (
                        MarketSnapshot(
                            "EURUSD",
                            closes[-1],
                            closes[-1] + Decimal("0.0001"),
                            now,
                            "synthetic-eval",
                            True,
                        ),
                    ),
                    now=now,
                )
                context = await ContextCompiler(
                    registry,
                    paper_context_sources(snapshot, engine.histories()),
                    clock=lambda: now,
                ).compile("A02", "0.2.0", task_id=case.id, scope="PAPER")
                start = perf_counter()
                receipt = await runtime.run(case.id, context)
                elapsed_ms = (perf_counter() - start) * 1000
                predicted = ()
                if receipt.output_json is not None:
                    output = json.loads(receipt.output_json)
                    instrument = next(
                        row for row in output["assessments"] if row["instrument_id"] == "EURUSD"
                    )
                    predicted = tuple(row["regime"] for row in instrument["horizons"])
                results.append(
                    {
                        "case_id": case.id,
                        "status": receipt.status,
                        "expected": case.expected,
                        "predicted": predicted,
                        "correct_horizons": sum(a == b for a, b in zip(case.expected, predicted)),
                        "elapsed_ms": elapsed_ms,
                        "context_ref": context.context_ref,
                    }
                )
    durations = sorted(row["elapsed_ms"] for row in results)
    return {
        "schema_version": "1.0",
        "agent_id": "A02",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "case_count": len(results),
        "horizon_count": 3 * len(results),
        "correct_horizons": sum(row["correct_horizons"] for row in results),
        "latency_p95_ms": durations[math.ceil(len(durations) * 0.95) - 1],
        "calibration_status": "UNAVAILABLE",
        "promotion_approved": False,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay synthetic A02 shadow baseline")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_regime_fixture(args.fixture))
    raw = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(raw, encoding="utf-8")
    else:
        print(raw)


if __name__ == "__main__":
    main()
