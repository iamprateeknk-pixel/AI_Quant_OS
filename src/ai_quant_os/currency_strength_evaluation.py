"""Replay declared synthetic A04 expectations through the actual persisted graph."""

import argparse
import asyncio
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
from ai_quant_os.currency_strength_agent import (
    CurrencyScoreVector,
    currency_strength_implementation,
)
from ai_quant_os.currency_strength_sources import (
    STRENGTH_INPUT_BYTES,
    STRENGTH_CONTEXT_BYTES,
    currency_strength_sources,
)
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import WorldStateEngine


class StrengthCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    prices: dict[str, tuple[Decimal, ...]]
    offset_seconds: dict[str, int] = Field(default_factory=dict)
    expected_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNALIGNED", "INCONSISTENT"]
    expected_basis: int = Field(ge=0, le=3)
    expected_scores: dict[str, Decimal | None]

    @model_validator(mode="after")
    def bounded_recipe(self) -> "StrengthCase":
        if (
            set(self.prices) - {item.instrument_id for item in PAPER_INSTRUMENTS}
            or set(self.expected_scores) != {"EUR", "GBP", "JPY", "USD"}
            or set(self.offset_seconds) - set(self.prices)
            or any(
                type(value) is not int or not 0 <= value <= 59
                for value in self.offset_seconds.values()
            )
            or any(not 3 <= len(rows) <= 64 for rows in self.prices.values())
            or any(
                not value.is_finite() or not Decimal("1e-12") <= value <= Decimal("1e12")
                for rows in self.prices.values()
                for value in rows
            )
            or any(
                value is not None and (not value.is_finite() or abs(value) > 1_000_000)
                for value in self.expected_scores.values()
            )
        ):
            raise ValueError("Invalid bounded currency-strength fixture")
        return self


class StrengthFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT"]
    cases: tuple[StrengthCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_cases(self) -> "StrengthFixture":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate currency-strength fixture case")
        return self


def _case_world(case: StrengthCase, now: datetime):
    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    bars = tuple(
        Bar(
            identifier,
            now
            - timedelta(seconds=len(prices) - index - 1 + case.offset_seconds.get(identifier, 0)),
            price,
            price,
            price,
            price,
            Decimal(1),
        )
        for identifier, prices in sorted(case.prices.items())
        for index, price in enumerate(prices)
    )
    engine.record_bars(bars, now=now)
    snapshot = engine.snapshot(
        tuple(
            MarketSnapshot(
                identifier, prices[-1], prices[-1], now, "synthetic-strength-baseline", True
            )
            for identifier, prices in sorted(case.prices.items())
        ),
        now=now,
    )
    return snapshot, engine.histories()


async def evaluate_currency_strength_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Currency-strength fixture exceeds byte budget")
    fixture = StrengthFixture.model_validate_json(raw)
    registry = load_agent_catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    results = []
    with TemporaryDirectory(prefix="ai-quant-strength-eval-") as directory:
        async with GraphRuntime.open(
            Path(directory) / "graph.sqlite3",
            registry=registry,
            implementations={("A04", "0.2.0"): currency_strength_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: now,
        ) as runtime:
            for case in fixture.cases:
                world, histories = _case_world(case, now)
                context = await ContextCompiler(
                    registry,
                    currency_strength_sources(world, histories),
                    clock=lambda: now,
                    max_input_bytes=STRENGTH_INPUT_BYTES,
                    max_context_bytes=STRENGTH_CONTEXT_BYTES,
                ).compile("A04", "0.2.0", task_id=case.id, scope="PAPER")
                started = perf_counter()
                receipt = await runtime.run(case.id, context)
                elapsed = (perf_counter() - started) * 1000
                output = (
                    CurrencyScoreVector.model_validate_json(receipt.output_json)
                    if receipt.output_json
                    else None
                )
                scores = (
                    {row.currency: row.score_bps for row in output.result.estimates}
                    if output
                    else {}
                )
                matching = (
                    output is not None
                    and receipt.status == "COMPLETED"
                    and output.result.status == case.expected_status
                    and len(output.result.basis_instruments) == case.expected_basis
                    and set(scores) == set(case.expected_scores)
                    and all(
                        scores[currency] is None
                        if expected is None
                        else scores[currency] is not None
                        and abs(scores[currency] - expected) <= Decimal("1e-8")
                        for currency, expected in case.expected_scores.items()
                    )
                )
                results.append(
                    {
                        "case_id": case.id,
                        "status": receipt.status,
                        "matches": matching,
                        "elapsed_ms": elapsed,
                        "context_ref": context.context_ref,
                        "expected_status": case.expected_status,
                        "expected_basis": case.expected_basis,
                        "expected_scores": {
                            key: str(value) if value is not None else None
                            for key, value in case.expected_scores.items()
                        },
                        "output": output.model_dump(mode="json") if output else None,
                    }
                )
    return {
        "schema_version": "1.0",
        "agent_id": "A04",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "currency_strength.py",
                "currency_strength_sources.py",
                "currency_strength_agent.py",
            )
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
    report = asyncio.run(evaluate_currency_strength_fixture(arguments.fixture))
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
