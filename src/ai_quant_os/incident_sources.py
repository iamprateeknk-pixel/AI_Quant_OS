"""Complete checked collection summaries and read-only paper incident evidence."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.health_contracts import FAILURE_STATES, HealthIncident
from ai_quant_os.macro_sources import MacroWorldEvidence
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.risk import SafetyState
from ai_quant_os.world_state import WorldState, fingerprint

INCIDENT_INPUT_BYTES = 65536
INCIDENT_CONTEXT_BYTES = 160000


class IncidentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime


class WatchdogEvidence(IncidentObservation):
    status: Literal["UNAVAILABLE", "RESTORED_ONLY", "MONITORED", "ERROR"]
    incident_count: int | None = Field(ge=0, le=1000)
    code_counts: dict[str, int] | None
    future_count: int | None = Field(ge=0, le=1000)
    collection_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    coverage: Literal["ALL_CHECKED_UNRESOLVED_LOCAL_INCIDENTS"] = (
        "ALL_CHECKED_UNRESOLVED_LOCAL_INCIDENTS"
    )
    independent_anchor: Literal[False] = False

    @model_validator(mode="after")
    def complete_summary(self):
        fields = (self.incident_count, self.code_counts, self.future_count, self.collection_sha256)
        if self.status == "UNAVAILABLE":
            if any(value is not None for value in fields):
                raise ValueError("Missing watchdog cannot claim incident evidence")
        elif any(value is None for value in fields) or (
            set(self.code_counts) != set(FAILURE_STATES)
            or any(
                type(count) is not int or not 0 <= count <= 1000
                for count in self.code_counts.values()
            )
            or sum(self.code_counts.values()) != self.incident_count
            or self.future_count > self.incident_count
        ):
            raise ValueError("Incomplete watchdog collection summary")
        return self


class BrokerEvidence(IncidentObservation):
    broker_id: Literal["PAPER"] = "PAPER"
    enabled: bool
    connection_state: Literal["CONNECTED", "DISCONNECTED"]
    instruments: tuple[Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"], ...]
    uncertain_orders: int = Field(ge=0, le=10000)
    order_count: int = Field(ge=0, le=10000)
    orders_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    telemetry: Literal["PAPER_SIMULATOR_FLAGS_ONLY"] = "PAPER_SIMULATOR_FLAGS_ONLY"
    live_broker_health: Literal["UNAVAILABLE"] = "UNAVAILABLE"

    @model_validator(mode="after")
    def bounded_orders(self):
        if self.uncertain_orders > self.order_count or self.instruments != (
            "EURJPY",
            "EURUSD",
            "GBPUSD",
            "USDJPY",
        ):
            raise ValueError("Unsupported paper broker collection")
        return self


class ReconciliationEvidence(IncidentObservation):
    local_fill_count: int = Field(ge=0, le=10000)
    broker_fill_count: int = Field(ge=0, le=10000)
    local_only_count: int = Field(ge=0, le=10000)
    broker_only_count: int = Field(ge=0, le=10000)
    conflicting_count: int = Field(ge=0, le=10000)
    future_fill_count: int = Field(ge=0, le=20000)
    matches: bool
    collection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    comparison: Literal["COMPLETE_PAPER_FILL_IDENTITY"] = "COMPLETE_PAPER_FILL_IDENTITY"

    @model_validator(mode="after")
    def coherent_counts(self):
        if (
            self.matches
            != (self.local_only_count == self.broker_only_count == self.conflicting_count == 0)
            or self.local_only_count + self.conflicting_count > self.local_fill_count
            or self.broker_only_count + self.conflicting_count > self.broker_fill_count
            or self.local_fill_count - self.local_only_count
            != self.broker_fill_count - self.broker_only_count
            or self.future_fill_count > self.local_fill_count + self.broker_fill_count
        ):
            raise ValueError("Reconciliation summary disagrees with complete counts")
        return self


class RiskEvidence(IncidentObservation):
    live_state: SafetyState
    durable_state: SafetyState
    collection_consistency: Literal["PROCESS_CAPTURE_NOT_ATOMIC_ACROSS_STORES"] = (
        "PROCESS_CAPTURE_NOT_ATOMIC_ACROSS_STORES"
    )


class IncidentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    watchdog_events: WatchdogEvidence
    broker_health: BrokerEvidence
    data_health: MacroWorldEvidence
    reconciliation: ReconciliationEvidence
    risk_state: RiskEvidence

    @model_validator(mode="after")
    def shared_observation(self):
        if any(
            row.as_of != self.data_health.world.as_of
            for row in (
                self.watchdog_events,
                self.broker_health,
                self.reconciliation,
                self.risk_state,
            )
        ):
            raise ValueError("Incident sources have different observation boundaries")
        return self


def reconciliation_evidence(
    local: tuple[PaperFill, ...], broker: tuple[PaperFill, ...], *, now: datetime
) -> ReconciliationEvidence:
    left, right = {row.fill_id: row for row in local}, {row.fill_id: row for row in broker}
    if len(left) != len(local) or len(right) != len(broker):
        raise ValueError("Duplicate incident fill identity")
    local_only, broker_only = len(left.keys() - right.keys()), len(right.keys() - left.keys())
    conflicts = sum(left[key] != right[key] for key in left.keys() & right.keys())
    return ReconciliationEvidence(
        as_of=now,
        local_fill_count=len(local),
        broker_fill_count=len(broker),
        local_only_count=local_only,
        broker_only_count=broker_only,
        conflicting_count=conflicts,
        future_fill_count=sum(row.observed_at > now for row in local + broker),
        matches=local_only == broker_only == conflicts == 0,
        collection_sha256=fingerprint(
            {
                "local": [asdict(left[key]) for key in sorted(left)],
                "broker": [asdict(right[key]) for key in sorted(right)],
            }
        ),
    )


def incident_sources(
    world: WorldState,
    *,
    incidents: tuple[HealthIncident, ...] | None,
    watchdog_status: str,
    broker: dict,
    orders: tuple,
    local_fills: tuple[PaperFill, ...],
    broker_fills: tuple[PaperFill, ...],
    live_state: SafetyState,
    durable_state: SafetyState,
) -> dict[str, SourceBinding]:
    now = world.as_of
    if (
        len(orders) > 10000
        or len(local_fills) > 10000
        or len(broker_fills) > 10000
        or (incidents is not None and len(incidents) > 1000)
    ):
        raise ValueError("Incident evidence collection exceeds its complete-source bound")
    if incidents is not None:
        for row in incidents:
            HealthIncident(**asdict(row))
        if len({row.incident_id for row in incidents}) != len(incidents):
            raise ValueError("Duplicate incident collection identity")
    counts = Counter(row.code for row in incidents or ())
    evidence = IncidentEvidence(
        watchdog_events=WatchdogEvidence(
            as_of=now,
            status=watchdog_status,
            incident_count=len(incidents) if incidents is not None else None,
            code_counts={code: counts[code] for code in FAILURE_STATES}
            if incidents is not None
            else None,
            future_count=sum(row.detected_at > now for row in incidents)
            if incidents is not None
            else None,
            collection_sha256=fingerprint(
                [asdict(row) for row in sorted(incidents, key=lambda row: row.incident_id)]
            )
            if incidents is not None
            else None,
        ),
        broker_health=BrokerEvidence(
            as_of=now,
            **broker,
            order_count=len(orders),
            uncertain_orders=sum(row.status in {"DISPATCHING", "UNKNOWN"} for row in orders),
            orders_sha256=fingerprint(
                [asdict(row) for row in sorted(orders, key=lambda row: row.order_id)]
            ),
        ),
        data_health=MacroWorldEvidence(world=world),
        reconciliation=reconciliation_evidence(local_fills, broker_fills, now=now),
        risk_state=RiskEvidence(as_of=now, live_state=live_state, durable_state=durable_state),
    )
    result = {}
    for name, tool in (
        ("watchdog_events", "HEALTH_READ"),
        ("broker_health", "HEALTH_READ"),
        ("data_health", "WORLD_STATE_READ"),
        ("reconciliation", "PORTFOLIO_READ"),
        ("risk_state", "HEALTH_READ"),
    ):
        record = EvidenceRecord(
            source_id=f"paper-incident:{name}:{fingerprint(getattr(evidence, name).model_dump(mode='json'))}",
            as_of=now,
            valid_until=world.valid_until,
            trusted=True,
            payload_json=getattr(evidence, name).model_dump_json(),
        )

        async def fetch(record=record):
            return record

        result[name] = SourceBinding(tool, "GENERAL", fetch)
    return result
