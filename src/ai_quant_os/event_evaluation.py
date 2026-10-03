"""Replay declared synthetic A06 expectations through the persisted paper graph."""

import argparse
import asyncio
from datetime import datetime, timezone
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
from ai_quant_os.event_agent import EventRiskState, event_implementation
from ai_quant_os.event_feed import EventFeed
from ai_quant_os.event_sources import EVENT_INPUT_BYTES, EVENT_CONTEXT_BYTES, event_sources
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


class EventCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    feed: EventFeed | None
    expected_feed_status: Literal["FRESH", "MISSING", "STALE", "FUTURE"]
    expected_blackouts: dict[str, bool | None]
    expected_scores: dict[str, Decimal | None] = Field(max_length=128)

    @model_validator(mode="after")
    def bounded_expectations(self) -> "EventCase":
        if set(self.expected_blackouts) != {row.instrument_id for row in PAPER_INSTRUMENTS} or any(
            len(key) > 64 or (value is not None and (not value.is_finite() or not 0 <= value <= 1))
            for key, value in self.expected_scores.items()
        ):
            raise ValueError("Invalid bounded event expectations")
        return self


class EventFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT"]
    cases: tuple[EventCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_cases(self) -> "EventFixture":
        if len({row.id for row in self.cases}) != len(self.cases):
            raise ValueError("Duplicate event fixture case")
        return self


def _matches(value: Decimal | None, expected: Decimal | None) -> bool:
    return (
        value is None
        if expected is None
        else value is not None and abs(value - expected) <= Decimal("1e-20")
    )


async def evaluate_event_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Event fixture exceeds byte budget")
    fixture = EventFixture.model_validate_json(raw)
    registry = load_agent_catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    results = []
    with TemporaryDirectory(prefix="ai-quant-event-eval-") as directory:
        async with GraphRuntime.open(
            Path(directory) / "graph.sqlite3",
            registry=registry,
            implementations={("A06", "0.2.0"): event_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: now,
        ) as runtime:
            for case in fixture.cases:
                context = await ContextCompiler(
                    registry,
                    event_sources(case.feed, (), (), now=now),
                    clock=lambda: now,
                    max_input_bytes=EVENT_INPUT_BYTES,
                    max_context_bytes=EVENT_CONTEXT_BYTES,
                ).compile("A06", "0.2.0", task_id=case.id, scope="PAPER")
                started = perf_counter()
                receipt = await runtime.run(case.id, context)
                elapsed = (perf_counter() - started) * 1000
                output = (
                    EventRiskState.model_validate_json(receipt.output_json)
                    if receipt.output_json
                    else None
                )
                blackouts = (
                    {row.instrument_id: row.blackout_recommended for row in output.assessments}
                    if output
                    else {}
                )
                scores = (
                    {row.event_id: row.uncalibrated_score for row in output.measurements}
                    if output
                    else {}
                )
                matching = (
                    receipt.status == "COMPLETED"
                    and output is not None
                    and output.feed_status == case.expected_feed_status
                    and blackouts == case.expected_blackouts
                    and set(scores) == set(case.expected_scores)
                    and all(
                        _matches(scores[key], expected)
                        for key, expected in case.expected_scores.items()
                    )
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
        "agent_id": "A06",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "event_risk.py",
                "event_feed.py",
                "event_feed_store.py",
                "event_feed_api.py",
                "event_sources.py",
                "event_agent.py",
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
    report = asyncio.run(evaluate_event_fixture(arguments.fixture))
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
