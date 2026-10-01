"""A07 local paper-feed integrity mission and fresh diagnostic source adapters."""

import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.ingress_health import CODES
from ai_quant_os.market_data import Bar
from ai_quant_os.world_state import WorldState, fingerprint


class FeedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str = Field(min_length=1, max_length=32)
    quote_status: Literal["MISSING", "UNTRUSTED", "FUTURE", "STALE", "FRESH"]
    history_status: Literal["INSUFFICIENT", "STALE", "FRESH"]


class FeedHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_state_version: str
    feeds: tuple[FeedObservation, ...] = Field(min_length=1, max_length=16)
    ingress_counts: dict[str, int]
    retention: Literal["PROCESS_LOCAL_SINCE_STARTUP"]


class LineageHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_state_version: str
    verified_instruments: tuple[str, ...]
    coverage: Literal["LOCAL_BAR_CONTENT_HASH_ONLY"]


class ClockHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_state_version: str
    status: Literal["UNVERIFIED", "MEASURED"]
    offset_ms: Decimal | None = Field(default=None, allow_inf_nan=False)


class IntegrityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    usable: bool
    issues: tuple[str, ...]
    affected_features: tuple[str, ...]


class IncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requested_mode: Literal["NO_NEW_RISK"] = "NO_NEW_RISK"
    automatic_control_change: Literal[False] = False
    reason: Literal["UNUSABLE_MARKET_EVIDENCE"] = "UNUSABLE_MARKET_EVIDENCE"


class DataHealth(AgentEnvelope):
    agent_id: Literal["A07"] = "A07"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["DataHealth"] = "DataHealth"
    status: Literal["UNUSABLE", "DEGRADED", "HEALTHY"]
    instruments: tuple[IntegrityFinding, ...]
    quarantined_instruments: tuple[str, ...]
    ingress_counts: dict[str, int]
    clock_status: Literal["UNVERIFIED", "MEASURED"]
    ingress_retention: Literal["PROCESS_LOCAL_SINCE_STARTUP"] = "PROCESS_LOCAL_SINCE_STARTUP"
    external_feed_coverage: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    incident_request: IncidentRequest | None


def integrity_sources(
    snapshot: WorldState, histories: dict[str, tuple[Bar, ...]], counts: dict[str, int]
) -> dict[str, SourceBinding]:
    verified = []
    for item in snapshot.instruments:
        bars = histories.get(item.instrument_id, ())
        if (
            bars
            and "paper-bars:" + fingerprint([asdict(bar) for bar in bars]) in item.evidence_refs
        ):
            verified.append(item.instrument_id)
    common = {"world_state_version": snapshot.world_state_version}
    payloads = {
        "feed_health": {
            **common,
            "feeds": [
                {
                    "instrument_id": row.instrument_id,
                    "quote_status": row.quote_status,
                    "history_status": row.history_status,
                }
                for row in snapshot.instruments
            ],
            "ingress_counts": counts,
            "retention": "PROCESS_LOCAL_SINCE_STARTUP",
        },
        "lineage": {
            **common,
            "verified_instruments": verified,
            "coverage": "LOCAL_BAR_CONTENT_HASH_ONLY",
        },
        "clock_health": {**common, "status": "UNVERIFIED", "offset_ms": None},
    }
    sources = {}
    for name, payload in payloads.items():

        async def fetch(name=name, payload=payload):
            return EvidenceRecord(
                source_id=f"paper-health:{name}:{snapshot.world_state_version}",
                as_of=snapshot.as_of,
                valid_until=snapshot.as_of + timedelta(seconds=5),
                trusted=True,
                payload_json=json.dumps(payload),
            )

        sources[name] = SourceBinding(
            "HEALTH_READ" if name != "lineage" else "HISTORY_READ", "GENERAL", fetch
        )
    return sources


async def assess_integrity(invocation: AgentInvocation) -> DataHealth:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A07", "0.2.0", "READY"):
        raise ValueError("Integrity mission context mismatch")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    feed = FeedHealth.model_validate(inputs["feed_health"])
    lineage = LineageHealth.model_validate(inputs["lineage"])
    clock = ClockHealth.model_validate(inputs["clock_health"])
    if (
        feed.world_state_version != lineage.world_state_version
        or feed.world_state_version != clock.world_state_version
        or len({row.instrument_id for row in feed.feeds}) != len(feed.feeds)
        or set(feed.ingress_counts) != CODES
        or any(type(x) is not int or not 0 <= x <= 2**31 - 1 for x in feed.ingress_counts.values())
        or (clock.status == "MEASURED") != (clock.offset_ms is not None)
    ):
        raise ValueError("Integrity source contract mismatch")
    findings = []
    for row in feed.feeds:
        issues, features = [], []
        if row.quote_status != "FRESH":
            issues.append("QUOTE_" + row.quote_status)
            features.append("QUOTE")
        if row.history_status != "FRESH":
            issues.append("HISTORY_" + row.history_status)
            features.append("HISTORY")
        elif row.instrument_id not in lineage.verified_instruments:
            issues.append("LINEAGE_MISMATCH")
            features.append("HISTORY")
        if clock.offset_ms is not None and abs(clock.offset_ms) > 1000:
            issues.append("CLOCK_OUT_OF_SYNC")
            features.extend(("QUOTE", "HISTORY"))
        findings.append(
            IntegrityFinding(
                instrument_id=row.instrument_id,
                usable=not issues,
                issues=tuple(issues),
                affected_features=tuple(sorted(set(features))),
            )
        )
    quarantined = tuple(row.instrument_id for row in findings if not row.usable)
    gaps = ["CALIBRATION_UNAVAILABLE", "EXTERNAL_LINEAGE_UNVERIFIED"]
    if clock.status == "UNVERIFIED":
        gaps.append("CLOCK_MEASUREMENT_UNAVAILABLE")
    gaps.extend("INGRESS_" + code for code, count in sorted(feed.ingress_counts.items()) if count)
    return DataHealth(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(gaps),
        trace_id=str(uuid4()),
        status="UNUSABLE" if len(quarantined) == len(findings) else "DEGRADED",
        instruments=tuple(findings),
        quarantined_instruments=quarantined,
        ingress_counts=feed.ingress_counts,
        clock_status=clock.status,
        incident_request=IncidentRequest() if quarantined else None,
    )


def integrity_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A07", "0.2.0").manifest_ref, DataHealth, assess_integrity
    )
