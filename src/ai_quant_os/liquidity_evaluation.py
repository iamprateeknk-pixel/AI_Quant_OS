"""Replay declared synthetic A05 expectations through the persisted paper graph."""

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.liquidity_agent import LiquidityState, liquidity_implementation
from ai_quant_os.liquidity_sources import (
    DisplayedDepth,
    LIQUIDITY_INPUT_BYTES,
    LIQUIDITY_CONTEXT_BYTES,
    liquidity_sources,
)
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine, fingerprint


class LiquidityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    prices: tuple[Decimal, ...] = Field(max_length=64)
    quote_age_seconds: int | None = Field(ge=0, le=60)
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    expected_quote_status: Literal["FRESH", "STALE", "MISSING"]
    expected_liquidity_status: Literal["UNAVAILABLE", "SYNTHETIC_L1_OBSERVED"]
    expected_abnormality_status: Literal[
        "AVAILABLE", "ZERO_VARIANCE_MATCH", "DEGENERATE_BASELINE", "INSUFFICIENT_BASELINE"
    ]
    expected_abnormality_score: Decimal | None = Field(ge=0, le=1, allow_inf_nan=False)
    expected_imbalance: Decimal | None = Field(ge=-1, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded_recipe(self) -> "LiquidityCase":
        if any(
            not value.is_finite()
            or not Decimal("1e-12") <= value <= Decimal("1e12")
            or len(value.as_tuple().digits) > 64
            for value in self.prices
        ):
            raise ValueError("Invalid bounded liquidity price")
        if (self.bid_size is None) != (self.ask_size is None):
            raise ValueError("Both displayed sizes are required")
        if self.bid_size is not None:
            if self.quote_age_seconds is None:
                raise ValueError("Displayed sizes require a quote")
            DisplayedDepth(
                instrument_id="EURUSD",
                quote_ref="recipe",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                bid_size=self.bid_size,
                ask_size=self.ask_size,
            )
        return self


class LiquidityFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT"]
    cases: tuple[LiquidityCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_cases(self) -> "LiquidityFixture":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate liquidity fixture case")
        return self


def _case_sources(case: LiquidityCase, now: datetime):
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    bars = tuple(
        Bar(
            "EURUSD",
            now - timedelta(seconds=len(case.prices) - index - 1),
            price,
            price,
            price,
            price,
            Decimal(1),
        )
        for index, price in enumerate(case.prices)
    )
    engine.record_bars(bars, now=now)
    quotes = (
        ()
        if case.quote_age_seconds is None
        else (
            MarketSnapshot(
                "EURUSD",
                Decimal("2"),
                Decimal("2.002"),
                now - timedelta(seconds=case.quote_age_seconds),
                "synthetic-liquidity-baseline",
                True,
            ),
        )
    )
    depth = (
        ()
        if case.bid_size is None
        else (
            DisplayedDepth(
                instrument_id="EURUSD",
                quote_ref="paper-quote:" + fingerprint(asdict(quotes[0])),
                observed_at=quotes[0].observed_at,
                bid_size=case.bid_size,
                ask_size=case.ask_size,
            ),
        )
    )
    return liquidity_sources(engine.snapshot(quotes, now=now), engine.histories(), quotes, depth)


def _matches(value: Decimal | None, expected: Decimal | None) -> bool:
    return (
        value is None
        if expected is None
        else value is not None and abs(value - expected) <= Decimal("1e-20")
    )


async def evaluate_liquidity_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Liquidity fixture exceeds byte budget")
    fixture = LiquidityFixture.model_validate_json(raw)
    registry = load_agent_catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    results = []
    with TemporaryDirectory(prefix="ai-quant-liquidity-eval-") as directory:
        async with GraphRuntime.open(
            Path(directory) / "graph.sqlite3",
            registry=registry,
            implementations={("A05", "0.2.0"): liquidity_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: now,
        ) as runtime:
            for case in fixture.cases:
                context = await ContextCompiler(
                    registry,
                    _case_sources(case, now),
                    clock=lambda: now,
                    max_input_bytes=LIQUIDITY_INPUT_BYTES,
                    max_context_bytes=LIQUIDITY_CONTEXT_BYTES,
                ).compile("A05", "0.2.0", task_id=case.id, scope="PAPER")
                started = perf_counter()
                receipt = await runtime.run(case.id, context)
                elapsed = (perf_counter() - started) * 1000
                output = (
                    LiquidityState.model_validate_json(receipt.output_json)
                    if receipt.output_json
                    else None
                )
                eur = (
                    next((row for row in output.assessments if row.instrument_id == "EURUSD"), None)
                    if output
                    else None
                )
                matching = (
                    receipt.status == "COMPLETED"
                    and eur is not None
                    and eur.quote_status == case.expected_quote_status
                    and eur.liquidity_status == case.expected_liquidity_status
                    and eur.volatility.abnormality_status == case.expected_abnormality_status
                    and _matches(eur.volatility.abnormality_score, case.expected_abnormality_score)
                    and _matches(eur.displayed_imbalance, case.expected_imbalance)
                )
                results.append(
                    {
                        "case_id": case.id,
                        "status": receipt.status,
                        "matches": matching,
                        "elapsed_ms": elapsed,
                        "context_ref": context.context_ref,
                        "expectations": case.model_dump(mode="json"),
                        "output": output.model_dump(mode="json") if output else None,
                    }
                )
    return {
        "schema_version": "1.0",
        "agent_id": "A05",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("volatility_features.py", "liquidity_sources.py", "liquidity_agent.py")
        },
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(results),
        "matching_cases": sum(row["matches"] for row in results),
        "calibration_status": "UNAVAILABLE",
        "promotion_approved": False,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = asyncio.run(evaluate_liquidity_fixture(arguments.fixture))
    raw = json.dumps(report, indent=2) + "\n"
    if arguments.output:
        with arguments.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
    else:
        print(raw)
    if report["matching_cases"] != report["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
