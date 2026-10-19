"""Operational agent outcome metrics rebuilt from verified durable graph receipts."""

import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_ledger import GraphReceipt, RunStatus

RUN_STATUSES = tuple(RunStatus.__args__)


class AgentOperationalQualityRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    run_count: StrictInt = Field(ge=1, le=10000)
    status_counts: dict[str, StrictInt]
    completion_rate: Decimal = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def counts_match(self):
        if (
            set(self.status_counts) != set(RUN_STATUSES)
            or any(type(value) is not int or value < 0 for value in self.status_counts.values())
            or sum(self.status_counts.values()) != self.run_count
            or self.completion_rate != Decimal(self.status_counts["COMPLETED"]) / self.run_count
        ):
            raise ValueError("Operational quality counts differ from graph receipts")
        return self


class AgentOperationalQualityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    as_of: AwareDatetime
    availability: Literal["AVAILABLE", "NO_OBSERVATIONS", "UNAVAILABLE"]
    reason: (
        Literal["NO_GRAPH_RUNS", "GRAPH_LEDGER_UNAVAILABLE", "GRAPH_RUN_CAPACITY_EXCEEDED"] | None
    ) = None
    source_receipt_count: StrictInt = Field(ge=0, le=10000)
    source_receipts_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rows: tuple[AgentOperationalQualityRow, ...] = Field(max_length=128)
    metric_scope: Literal["OPERATIONAL_OUTCOME_COUNTS"] = "OPERATIONAL_OUTCOME_COUNTS"
    calibration_established: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def consistent_inventory(self):
        def key(row):
            return row.agent_id, row.agent_version

        counts = sum(row.run_count for row in self.rows)
        if (
            tuple(sorted(self.rows, key=key)) != self.rows
            or len({key(row) for row in self.rows}) != len(self.rows)
            or counts != self.source_receipt_count
            or (self.availability == "AVAILABLE" and (counts == 0 or self.reason is not None))
            or (
                self.availability == "NO_OBSERVATIONS"
                and (counts != 0 or self.reason != "NO_GRAPH_RUNS")
            )
            or (
                self.availability == "UNAVAILABLE"
                and (
                    counts != 0
                    or self.reason
                    not in {"GRAPH_LEDGER_UNAVAILABLE", "GRAPH_RUN_CAPACITY_EXCEEDED"}
                )
            )
        ):
            raise ValueError("Operational quality availability differs from receipt inventory")
        return self


def operational_quality_observation(
    registry: AgentRegistry,
    receipts: tuple[object, ...] | None,
    *,
    now: datetime,
    unavailable_reason: Literal["GRAPH_LEDGER_UNAVAILABLE", "GRAPH_RUN_CAPACITY_EXCEEDED"] = (
        "GRAPH_LEDGER_UNAVAILABLE"
    ),
) -> AgentOperationalQualityObservation:
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("Current agent quality observation time required")
    if type(registry) is not AgentRegistry:
        raise ValueError("Server-owned agent registry required")
    if receipts is None:
        return AgentOperationalQualityObservation(
            as_of=now,
            availability="UNAVAILABLE",
            reason=unavailable_reason,
            source_receipt_count=0,
            source_receipts_sha256=sha256(b"graph-ledger-unavailable").hexdigest(),
            rows=(),
        )
    if type(receipts) is not tuple or len(receipts) > 10000:
        raise ValueError("Bounded durable graph receipt tuple required")
    unique = set()
    groups = defaultdict(list)
    canonical_receipts = []
    for receipt in receipts:
        if type(receipt) is not GraphReceipt:
            raise ValueError("Typed graph receipts required for agent quality metrics")
        if receipt.run_id in unique:
            raise ValueError("duplicate graph receipt in quality observation")
        unique.add(receipt.run_id)
        manifest = registry.get(receipt.agent_id, receipt.agent_version)
        if manifest.manifest_ref != receipt.manifest_ref:
            raise ValueError("Graph quality receipt manifest pin mismatch")
        if receipt.created_at > now:
            raise ValueError("Future graph receipt cannot enter quality observation")
        identity = (receipt.agent_id, receipt.agent_version)
        groups[identity].append(receipt)
        canonical_receipts.append(
            {
                "agent_id": receipt.agent_id,
                "agent_version": receipt.agent_version,
                "manifest_ref": receipt.manifest_ref,
                "run_id": receipt.run_id,
                "status": receipt.status,
            }
        )
    rows = []
    for (agent_id, version), observed in sorted(groups.items()):
        manifest = registry.get(agent_id, version)
        status_counts = {status: 0 for status in RUN_STATUSES}
        for receipt in observed:
            status_counts[receipt.status] += 1
        rows.append(
            AgentOperationalQualityRow(
                agent_id=agent_id,
                agent_version=version,
                manifest_ref=manifest.manifest_ref,
                run_count=len(observed),
                status_counts=status_counts,
                completion_rate=Decimal(status_counts["COMPLETED"]) / len(observed),
            )
        )
    canonical_receipts.sort(key=lambda row: row["run_id"])
    digest = sha256(
        json.dumps(canonical_receipts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not receipts:
        return AgentOperationalQualityObservation(
            as_of=now,
            availability="NO_OBSERVATIONS",
            reason="NO_GRAPH_RUNS",
            source_receipt_count=0,
            source_receipts_sha256=digest,
            rows=(),
        )
    return AgentOperationalQualityObservation(
        as_of=now,
        availability="AVAILABLE",
        source_receipt_count=len(receipts),
        source_receipts_sha256=digest,
        rows=tuple(rows),
    )
