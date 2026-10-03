"""Declared development replay of A03 over retained ECB and synthetic context facts."""

import argparse
import asyncio
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.event_feed import EventFeed
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.macro_agent import CurrencyMacroBias, macro_implementation
from ai_quant_os.macro_sources import MACRO_INPUT_BYTES, MACRO_CONTEXT_BYTES, macro_sources
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.policy_rates import RateCapture
from ai_quant_os.world_state import WorldStateEngine


class MacroCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    as_of: AwareDatetime
    capture: RateCapture | None
    feed: EventFeed | None
    expected_receipt_status: Literal["MISSING", "FUTURE_RECEIPT", "OLD_RECEIPT", "RECENT_RECEIPT"]
    expected_basis_points: dict[str, Decimal | None] = Field(max_length=3)
    expected_event_counts: dict[str, int | None]

    @model_validator(mode="after")
    def bounded_expectations(self) -> "MacroCase":
        if (
            not set(self.expected_basis_points) <= {"DFR", "MLFR", "MRR_FR"}
            or any(
                value is not None and (not value.is_finite() or not -20000 <= value <= 20000)
                for value in self.expected_basis_points.values()
            )
            or set(self.expected_event_counts) != {"EUR", "GBP", "JPY", "USD"}
            or any(
                value is not None and not 0 <= value <= 128
                for value in self.expected_event_counts.values()
            )
        ):
            raise ValueError("Invalid macro expected measurements")
        return self


class MacroFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal[
        "DEVELOPMENT_REPLAY_WITH_RETAINED_ECB_AND_SYNTHETIC_WORLD_EVENTS_NOT_HOLDOUT"
    ]
    cases: tuple[MacroCase, ...] = Field(min_length=1, max_length=32)


async def evaluate_macro_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Macro fixture exceeds budget")
    fixture = MacroFixture.model_validate_json(raw)
    if len({case.id for case in fixture.cases}) != len(fixture.cases):
        raise ValueError("Duplicate macro case identity")
    results = []
    with TemporaryDirectory(prefix="macro-eval-") as directory:
        for case in fixture.cases:
            registry = load_agent_catalog()
            world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=case.as_of)
            context = await ContextCompiler(
                registry,
                macro_sources(world, case.capture, case.feed),
                clock=lambda: case.as_of,
                max_input_bytes=MACRO_INPUT_BYTES,
                max_context_bytes=MACRO_CONTEXT_BYTES,
            ).compile("A03", "0.2.0", task_id=case.id, scope="PAPER")
            async with GraphRuntime.open(
                Path(directory) / (case.id + ".sqlite3"),
                registry=registry,
                implementations={("A03", "0.2.0"): macro_implementation(registry)},
                tools=AgentToolBoundary(registry, {}),
                clock=lambda: case.as_of,
            ) as runtime:
                started = perf_counter()
                receipt = await runtime.run(case.id, context)
                elapsed = (perf_counter() - started) * 1000
            output = (
                CurrencyMacroBias.model_validate_json(receipt.output_json)
                if receipt.status == "COMPLETED"
                else None
            )
            changes = (
                {row.rate_id: row.change_basis_points for row in output.drivers} if output else {}
            )
            counts = (
                {
                    row.currency: row.scheduled_event_count + row.detected_event_count
                    if row.scheduled_event_count is not None
                    else None
                    for row in output.currencies
                }
                if output
                else {}
            )
            matching = (
                output is not None
                and output.rate_receipt_status == case.expected_receipt_status
                and changes == case.expected_basis_points
                and counts == case.expected_event_counts
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
        "agent_id": "A03",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("macro_drivers.py", "macro_sources.py", "macro_agent.py")
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
    report = asyncio.run(evaluate_macro_fixture(arguments.fixture))
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
