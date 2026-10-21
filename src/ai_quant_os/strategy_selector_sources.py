"""Explicit, source-bound selector inputs with unavailable metrics kept visible."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from hashlib import sha256
import json
import threading
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.research_candidate_store import CandidateView
from ai_quant_os.world_state import WorldState

INPUT_NAMES = ("approved_strategy_registry", "world_state", "performance", "drift")
_SHADOW_SCAN_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="selector-shadow")
_SHADOW_SCAN_SLOTS = threading.BoundedSemaphore(2)


class ShadowScanCapacityExceeded(RuntimeError):
    """Raised when all bounded selector shadow scan workers are occupied."""


async def run_shadow_performance_scan(store, *, now: datetime) -> "ShadowPerformanceObservation":
    if not _SHADOW_SCAN_SLOTS.acquire(blocking=False):
        raise ShadowScanCapacityExceeded("Selector shadow scan capacity exceeded")
    try:
        future = _SHADOW_SCAN_EXECUTOR.submit(
            partial(shadow_performance_observation, store, now=now)
        )
    except BaseException:
        _SHADOW_SCAN_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _SHADOW_SCAN_SLOTS.release())
    asyncio_future = asyncio.wrap_future(future, loop=asyncio.get_running_loop())
    return await asyncio.shield(asyncio_future)


class CandidateRegistryRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal[
        "APPROVED", "CHALLENGER", "FAILED", "REJECTED", "WITHDRAWN", "DEMOTED", "DEMOTED"
    ]
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


class ShadowPerformanceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    shadow_id: str = Field(min_length=1, max_length=64)
    assignment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sequence: int = Field(strict=True, ge=0, le=1000)
    outcome: Literal["NOT_OBSERVED", "OBSERVED", "FAILED"]
    observed_at: AwareDatetime | None
    pnl_currency: str | None = Field(default=None, min_length=3, max_length=8)
    net_pnl: Decimal | None = Field(default=None, allow_inf_nan=False)
    report_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_reason: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent_report(self):
        if self.outcome == "NOT_OBSERVED":
            valid = (
                self.sequence == 0
                and self.observed_at is None
                and self.pnl_currency is None
                and self.net_pnl is None
                and self.report_sha256 is None
                and self.failure_reason is None
            )
        elif self.outcome == "OBSERVED":
            valid = (
                self.sequence > 0
                and self.observed_at is not None
                and self.pnl_currency is not None
                and self.net_pnl is not None
                and self.report_sha256 is not None
                and self.failure_reason is None
            )
        else:
            valid = (
                self.sequence > 0
                and self.observed_at is not None
                and ((self.net_pnl is None) == (self.pnl_currency is None))
                and self.report_sha256 is not None
                and self.failure_reason is not None
            )
        if not valid:
            raise ValueError("Shadow performance row differs from its retained report")
        return self


class ShadowPerformanceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["AVAILABLE", "NO_OBSERVATIONS", "UNAVAILABLE"]
    reason: str | None = Field(default=None, min_length=1, max_length=128)
    rows: tuple[ShadowPerformanceRow, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def consistent_availability(self):
        has_observations = any(row.outcome == "OBSERVED" for row in self.rows)
        if (
            (self.availability == "AVAILABLE" and (not has_observations or self.reason is not None))
            or (
                self.availability == "NO_OBSERVATIONS"
                and (has_observations or self.reason is not None)
            )
            or (self.availability == "UNAVAILABLE" and (self.rows or self.reason is None))
        ):
            raise ValueError("Shadow performance availability differs from retained evidence")
        if any(row.observed_at is not None and row.observed_at > self.as_of for row in self.rows):
            raise ValueError("Shadow performance cannot include future reports")
        return self

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RegistrySnapshot:
    available: bool
    candidates: tuple[CandidateView, ...]


def shadow_performance_observation(store, *, now: datetime) -> ShadowPerformanceObservation:
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("Current shadow performance observation time required")
    if store is None:
        return ShadowPerformanceObservation(
            as_of=now, availability="UNAVAILABLE", reason="SHADOW_STORE_UNAVAILABLE", rows=()
        )

    try:
        shadow_ids = store.assigned_ids(limit=100)
    except ValueError as error:
        if str(error) != "Shadow assignment inventory exceeds configured limit":
            raise
        return ShadowPerformanceObservation(
            as_of=now,
            availability="UNAVAILABLE",
            reason="SHADOW_ASSIGNMENT_CAPACITY_EXCEEDED",
            rows=(),
        )

    rows = []
    from ai_quant_os.research_store import encode_research

    for shadow_id in shadow_ids:
        assignment = store.assignment(shadow_id)
        if assignment.assigned_at > now:
            return ShadowPerformanceObservation(
                as_of=now,
                availability="UNAVAILABLE",
                reason="SHADOW_SNAPSHOT_ADVANCED_SINCE_CONTEXT",
                rows=(),
            )
        reports = store.history(shadow_id)
        assignment_sha256 = sha256(encode_research(assignment)).hexdigest()
        if not reports:
            rows.append(
                ShadowPerformanceRow(
                    candidate_id=assignment.candidate.manifest.candidate_id,
                    shadow_id=shadow_id,
                    assignment_sha256=assignment_sha256,
                    sequence=0,
                    outcome="NOT_OBSERVED",
                    observed_at=None,
                )
            )
            continue
        report = reports[-1]
        if report.observed_at > now:
            return ShadowPerformanceObservation(
                as_of=now,
                availability="UNAVAILABLE",
                reason="SHADOW_SNAPSHOT_ADVANCED_SINCE_CONTEXT",
                rows=(),
            )
        run = report.run
        rows.append(
            ShadowPerformanceRow(
                candidate_id=assignment.candidate.manifest.candidate_id,
                shadow_id=shadow_id,
                assignment_sha256=assignment_sha256,
                sequence=report.sequence,
                outcome=report.outcome,
                observed_at=report.observed_at,
                pnl_currency=run.result.pnl_currency if run is not None else None,
                net_pnl=run.result.net_pnl if run is not None else None,
                report_sha256=report.fingerprint,
                failure_reason=report.reason,
            )
        )
    rows.sort(key=lambda row: (row.candidate_id, row.shadow_id))
    if len(rows) > 100:
        return ShadowPerformanceObservation(
            as_of=now,
            availability="UNAVAILABLE",
            reason="SHADOW_ASSIGNMENT_CAPACITY_EXCEEDED",
            rows=(),
        )
    availability = (
        "AVAILABLE" if any(row.outcome == "OBSERVED" for row in rows) else "NO_OBSERVATIONS"
    )
    return ShadowPerformanceObservation(as_of=now, availability=availability, rows=tuple(rows))


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
    shadow_store=None,
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


def selector_sources_v3(
    candidates: tuple[CandidateView, ...],
    snapshot: WorldState,
    *,
    now: datetime,
    registry_available: bool,
    shadow_store=None,
) -> dict[str, SourceBinding]:
    """Build A12 0.3.0 sources with verified shadow history as diagnostic evidence."""
    sources = selector_sources(candidates, snapshot, now=now, registry_available=registry_available)

    async def fetch():
        try:
            performance = await run_shadow_performance_scan(shadow_store, now=now)
        except ShadowScanCapacityExceeded:
            performance = ShadowPerformanceObservation(
                as_of=now,
                availability="UNAVAILABLE",
                reason="SHADOW_SCAN_CAPACITY_EXCEEDED",
                rows=(),
            )
        encoded = json.dumps(
            performance.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        record = EvidenceRecord(
            source_id=f"strategy-selector:performance:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )
        return record

    sources["performance"] = SourceBinding("ATTRIBUTION_READ", "GENERAL", fetch)
    return sources
