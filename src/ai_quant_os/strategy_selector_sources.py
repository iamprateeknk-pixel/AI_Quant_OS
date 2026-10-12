"""Explicit, source-bound selector inputs with unavailable metrics kept visible."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.research_candidate_store import CandidateView
from ai_quant_os.world_state import WorldState

INPUT_NAMES = ("approved_strategy_registry", "world_state", "performance", "drift")


class CandidateRegistryRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["APPROVED", "CHALLENGER", "FAILED", "REJECTED", "WITHDRAWN"]
    research_outcome: Literal["FAILED", "REJECTED", "VALIDATION_INCOMPLETE", "VALIDATED"]
    blocking_gates: tuple[str, ...] = Field(max_length=15)
    revision: int = Field(strict=True, ge=1, le=100000)


class CandidateRegistryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    available: StrictBool
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidates: tuple[CandidateRegistryRow, ...] = Field(max_length=1000)


class WorldObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    world_state_version: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    valid_until: AwareDatetime
    instruments: tuple[tuple[str, str, str], ...] = Field(max_length=32)


class UnavailableMetricObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["performance", "drift"]
    availability: Literal["UNCONNECTED", "UNASSESSED"]
    reason: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class RegistrySnapshot:
    available: bool
    candidates: tuple[CandidateView, ...]


def registry_observation(snapshot: RegistrySnapshot, *, now: datetime):
    rows = tuple(
        CandidateRegistryRow(
            candidate_id=view.manifest.candidate_id,
            state=view.state,
            research_outcome=view.manifest.research_outcome,
            blocking_gates=view.manifest.blocking_gates,
            revision=view.revision,
        )
        for view in sorted(snapshot.candidates, key=lambda item: item.manifest.candidate_id)
    )
    canonical = json.dumps(
        {
            "available": snapshot.available,
            "candidates": [row.model_dump(mode="json") for row in rows],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return CandidateRegistryObservation(
        as_of=now,
        available=snapshot.available,
        registry_sha256=sha256(canonical).hexdigest(),
        candidates=rows,
    )


def selector_sources(
    candidates: tuple[CandidateView, ...],
    snapshot: WorldState,
    *,
    now: datetime,
    registry_available: bool,
) -> dict[str, SourceBinding]:
    if type(now) is not datetime or now.tzinfo is None or type(snapshot) is not WorldState:
        raise ValueError("Current paper selector evidence required")
    registry = registry_observation(RegistrySnapshot(registry_available, candidates), now=now)
    world = WorldObservation(
        as_of=now,
        world_state_version=snapshot.world_state_version,
        status=snapshot.status,
        valid_until=snapshot.valid_until,
        instruments=tuple(
            (row.instrument_id, row.quote_status, row.regime)
            for row in sorted(snapshot.instruments, key=lambda item: item.instrument_id)
        ),
    )
    payloads = {
        "approved_strategy_registry": (registry, "STRATEGY_REGISTRY_READ"),
        "world_state": (world, "WORLD_STATE_READ"),
        "performance": (
            UnavailableMetricObservation(
                as_of=now,
                input_name="performance",
                availability="UNCONNECTED",
                reason="CANDIDATE_KEYED_SHADOW_PERFORMANCE_UNAVAILABLE",
            ),
            "ATTRIBUTION_READ",
        ),
        "drift": (
            UnavailableMetricObservation(
                as_of=now,
                input_name="drift",
                availability="UNASSESSED",
                reason="CANDIDATE_KEYED_DRIFT_UNASSESSED",
            ),
            "ATTRIBUTION_READ",
        ),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        encoded = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        record = EvidenceRecord(
            source_id=f"strategy-selector:{name}:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
