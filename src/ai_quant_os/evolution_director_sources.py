"""Registry inventory and explicit missing inputs for the A17 learning director."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from ai_quant_os.agent_quality import operational_quality_observation
from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentRegistry

INPUT_NAMES = ("quality_metrics", "training_datasets", "costs", "traces", "agent_registry")


class EvolutionEvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["quality_metrics", "training_datasets", "costs", "traces"]
    availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal[
        "CALIBRATED_QUALITY_METRICS_NOT_CONNECTED",
        "TRAINING_DATASET_CATALOG_NOT_CONNECTED",
        "TOKEN_AND_MONETARY_COSTS_NOT_CONNECTED",
        "CAUSAL_TRACE_QUALITY_NOT_CONNECTED",
    ]


class AgentRegistryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_count: StrictInt = Field(ge=1, le=128)
    partial_mission_count: StrictInt = Field(ge=0, le=128)
    planned_mission_count: StrictInt = Field(ge=0, le=128)


def evolution_sources(
    registry: AgentRegistry,
    *,
    now: datetime,
    quality_receipts: tuple[object, ...] | None = None,
    quality_traces: tuple[object, ...] | None = None,
    quality_unavailable_reason="GRAPH_LEDGER_UNAVAILABLE",
) -> dict[str, SourceBinding]:
    if type(now) is not datetime or now.tzinfo is None or type(registry) is not AgentRegistry:
        raise ValueError("Current server-owned evolution evidence required")
    manifests = registry.manifests()
    rows = tuple(
        {
            "agent_id": row.agent_id,
            "agent_version": row.agent_version,
            "manifest_ref": row.manifest_ref,
            "implementation_status": row.implementation_status,
            "lifecycle": registry.lifecycle(row.agent_id, row.agent_version),
        }
        for row in manifests
    )
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    inventory = AgentRegistryObservation(
        as_of=now,
        registry_sha256=sha256(canonical).hexdigest(),
        manifest_count=len(rows),
        partial_mission_count=sum(row["implementation_status"] == "PARTIAL" for row in rows),
        planned_mission_count=sum(row["implementation_status"] == "PLANNED" for row in rows),
    )
    quality = operational_quality_observation(
        registry,
        quality_receipts,
        traces=quality_traces,
        now=now,
        unavailable_reason=quality_unavailable_reason,
    )
    gaps = {
        "training_datasets": "TRAINING_DATASET_CATALOG_NOT_CONNECTED",
        "costs": "TOKEN_AND_MONETARY_COSTS_NOT_CONNECTED",
        "traces": "CAUSAL_TRACE_QUALITY_NOT_CONNECTED",
    }
    payloads = {
        name: (
            EvolutionEvidenceGap(as_of=now, input_name=name, reason=reason),
            "ATTRIBUTION_READ" if name in {"quality_metrics", "costs"} else "HEALTH_READ",
        )
        for name, reason in gaps.items()
    }
    payloads["quality_metrics"] = (
        quality,
        "ATTRIBUTION_READ",
    )
    payloads["agent_registry"] = (inventory, "HEALTH_READ")
    sources = {}
    for name, (payload, tool) in payloads.items():
        encoded = json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        record = EvidenceRecord(
            source_id=f"evolution-director:{name}:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
