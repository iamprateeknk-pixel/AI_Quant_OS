"""Task-bound paper execution ledger and explicit unavailable A14 evidence."""

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext, EvidenceRecord, SourceBinding, verify_context
from ai_quant_os.execution_attribution import DecisionPriceBenchmark
from ai_quant_os.execution_drift import DriftPolicy, ExecutionDriftReport, compare_execution_drift
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.world_state import fingerprint

INPUT_NAMES = ("shadow_results", "live_results", "fills", "predictions", "versions")


def _tool(name: str) -> str:
    return "PORTFOLIO_READ" if name == "fills" else "ATTRIBUTION_READ"


class DriftLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    policy: DriftPolicy | None
    owners: tuple[OrderAttribution, ...] = Field(max_length=512)
    fills: tuple[PaperFill, ...] = Field(max_length=512)
    benchmarks: tuple[DecisionPriceBenchmark, ...] = Field(max_length=512)

    @model_validator(mode="after")
    def checked(self) -> "DriftLedger":
        compare_execution_drift(
            self.owners, self.fills, self.benchmarks, now=self.as_of, policy=self.policy
        )
        return self

    @property
    def content_ref(self) -> str:
        return "paper-drift-ledger:" + fingerprint(self.model_dump(mode="json"))


class DriftDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    ledger_ref: str = Field(pattern=r"^paper-drift-ledger:[a-f0-9]{64}$")
    input_name: Literal["shadow_results", "live_results", "predictions", "versions"]
    availability: Literal["UNCONNECTED", "LOCAL_PAPER_LEDGER"]
    versions: tuple[tuple[str, str], ...] = Field(max_length=512)


def _payloads(ledger: DriftLedger) -> dict[str, DriftLedger | DriftDescriptor]:
    versions = tuple(sorted({(row.strategy_id, row.strategy_version) for row in ledger.owners}))
    return {
        name: ledger
        if name == "fills"
        else DriftDescriptor(
            as_of=ledger.as_of,
            ledger_ref=ledger.content_ref,
            input_name=name,
            availability="LOCAL_PAPER_LEDGER" if name == "versions" else "UNCONNECTED",
            versions=versions if name == "versions" else (),
        )
        for name in INPUT_NAMES
    }


def drift_sources(
    owners: tuple[OrderAttribution, ...],
    fills: tuple[PaperFill, ...],
    benchmarks: tuple[DecisionPriceBenchmark, ...],
    *,
    now: datetime,
    policy: DriftPolicy | None,
) -> dict[str, SourceBinding]:
    ledger = DriftLedger(
        as_of=now, policy=policy, owners=owners, fills=fills, benchmarks=benchmarks
    )
    sources = {}
    for name, payload in _payloads(ledger).items():
        record = EvidenceRecord(
            source_id="paper-drift:" + name + ":" + fingerprint(payload.model_dump(mode="json")),
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(_tool(name), "GENERAL", fetch)
    return sources


def report_for_context(context: CompiledContext) -> ExecutionDriftReport:
    verify_context(context)
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A14",
        "0.2.0",
        "PAPER",
        "READY",
    ) or tuple(row.input_name for row in context.inputs) != INPUT_NAMES:
        raise ValueError("Drift mission requires its complete paper context")
    rows = {row.input_name: row for row in context.inputs}
    ledger = DriftLedger.model_validate_json(rows["fills"].payload_json)
    for name, payload in _payloads(ledger).items():
        row = rows[name]
        original = EvidenceRecord(
            source_id="paper-drift:" + name + ":" + fingerprint(payload.model_dump(mode="json")),
            as_of=ledger.as_of,
            valid_until=ledger.as_of + timedelta(seconds=5),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )
        if (
            row.content_ref,
            row.source_id,
            row.source_tool,
            row.resource_class,
            row.as_of,
            row.valid_until,
            row.payload_json,
        ) != (
            original.content_ref,
            original.source_id,
            _tool(name),
            "GENERAL",
            original.as_of,
            original.valid_until,
            original.payload_json,
        ):
            raise ValueError("Drift context source identity mismatch")
    return compare_execution_drift(
        ledger.owners, ledger.fills, ledger.benchmarks, now=ledger.as_of, policy=ledger.policy
    )


def drift_configuration(declared: DriftPolicy | None) -> DriftPolicy | None:
    if declared is not None:
        if type(declared) is not DriftPolicy:
            raise ValueError("Typed declared drift policy required")
        return declared
    path = os.getenv("AI_QUANT_DRIFT_POLICY")
    if not path:
        return None

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate drift policy field")
            result[key] = value
        return result

    with Path(path).open("rb") as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError("Drift policy capacity exceeded")
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except RecursionError:
        raise ValueError("Drift policy nesting exceeded") from None
    if (
        type(value) is not dict
        or set(value) != {"window_seconds", "minimum_fills", "adverse_cost_increase_bps"}
        or type(value["adverse_cost_increase_bps"]) is not str
    ):
        raise ValueError("Exact declared drift policy fields required")
    try:
        return DriftPolicy(
            value["window_seconds"],
            value["minimum_fills"],
            Decimal(value["adverse_cost_increase_bps"]),
        )
    except InvalidOperation:
        raise ValueError("Invalid drift threshold encoding") from None
