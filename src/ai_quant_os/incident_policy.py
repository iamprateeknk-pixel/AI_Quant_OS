"""Descriptive local incident policy; returns recommendations without control authority."""

from dataclasses import dataclass

from ai_quant_os.health_contracts import FAILURE_STATES
from ai_quant_os.risk import SafetyState


@dataclass(frozen=True)
class IncidentFacts:
    live_state: SafetyState
    durable_state: SafetyState
    watchdog_available: bool
    watchdog_error: bool
    watchdog_codes: tuple[str, ...]
    future_incidents: int
    broker_available: bool
    uncertain_orders: int
    quote_statuses: tuple[str, ...]
    history_statuses: tuple[str, ...]
    reconciliation_matches: bool
    future_fills: int


@dataclass(frozen=True)
class IncidentAssessment:
    incident_class: str
    recommended_state: SafetyState
    findings: tuple[str, ...]
    workflow: str


def assess_incident(facts: IncidentFacts) -> IncidentAssessment:
    if any(not isinstance(state, SafetyState) for state in (facts.live_state, facts.durable_state)):
        raise ValueError("Invalid incident safety state")
    if any(
        type(flag) is not bool
        for flag in (
            facts.watchdog_available,
            facts.watchdog_error,
            facts.broker_available,
            facts.reconciliation_matches,
        )
    ):
        raise ValueError("Invalid incident availability flag")
    if any(
        type(count) is not int or not 0 <= count <= bound
        for count, bound in (
            (facts.future_incidents, 1000),
            (facts.uncertain_orders, 10000),
            (facts.future_fills, 20000),
        )
    ):
        raise ValueError("Invalid incident observation count")
    if any(
        type(statuses) is not tuple or len(statuses) != 4
        for statuses in (
            facts.quote_statuses,
            facts.history_statuses,
        )
    ):
        raise ValueError("Incomplete incident instrument observations")
    findings = set(facts.watchdog_codes)
    if not findings <= set(FAILURE_STATES):
        raise ValueError("Unknown retained incident code")
    if not facts.watchdog_available:
        findings.add("WATCHDOG_UNAVAILABLE")
    if facts.watchdog_error:
        findings.add("WATCHDOG_FAILURE")
    if facts.future_incidents or facts.future_fills:
        findings.add("FUTURE_LEDGER_OBSERVATION")
    if not facts.broker_available or facts.uncertain_orders:
        findings.add("BROKER_UNCONFIRMED")
    if not facts.reconciliation_matches:
        findings.add("POSITION_MISMATCH")
    if facts.live_state != facts.durable_state:
        findings.add("RISK_STATE_MISMATCH")
    for status in facts.quote_statuses:
        if status not in {"MISSING", "FRESH", "STALE", "FUTURE", "UNTRUSTED"}:
            raise ValueError("Unsupported incident quote status")
        if status != "FRESH":
            findings.add("QUOTE_" + status)
    for status in facts.history_statuses:
        if status not in {"INSUFFICIENT", "STALE", "FRESH"}:
            raise ValueError("Unsupported incident history status")
        if status != "FRESH":
            findings.add("HISTORY_" + status)
    states = list(SafetyState)
    floor = max((facts.live_state, facts.durable_state), key=states.index)
    for code in findings:
        required = SafetyState(
            FAILURE_STATES.get(
                code,
                "HALT"
                if code in {"RISK_STATE_MISMATCH", "FUTURE_LEDGER_OBSERVATION"}
                else "NO_NEW_RISK",
            )
        )
        floor = max((floor, required), key=states.index)
    severity = (
        "CRITICAL"
        if floor == SafetyState.HALT
        else "WARNING"
        if any(code in FAILURE_STATES for code in findings)
        else "DEGRADED_EVIDENCE"
        if findings
        else "NO_NEW_INCIDENT_FOUND"
    )
    workflow = (
        "RECONCILE_PAPER_RECORDS"
        if "POSITION_MISMATCH" in findings
        else "INSPECT_UNCERTAIN_PAPER_ORDER"
        if "BROKER_UNCONFIRMED" in findings
        else "OPERATOR_EVIDENCE_REVIEW"
    )
    return IncidentAssessment(severity, floor, tuple(sorted(findings)), workflow)
