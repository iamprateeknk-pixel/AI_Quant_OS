"""Deterministic local failure taxonomy and immutable first-observation evidence."""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType

from ai_quant_os.contracts import _require_aware


FAILURE_STATES = MappingProxyType(
    {
        "AUDIT_INVALID": "HALT",
        "GRAPH_INTEGRITY_FAILED": "HALT",
        "POSITION_MISMATCH": "HALT",
        "WATCHDOG_FAILURE": "HALT",
        "CLOCK_REGRESSION": "HALT",
        "BROKER_UNCONFIRMED": "NO_NEW_RISK",
        "QUOTE_STALE": "NO_NEW_RISK",
        "QUOTE_FUTURE": "NO_NEW_RISK",
        "QUOTE_UNTRUSTED": "NO_NEW_RISK",
        "AGENT_FAILED": "NO_NEW_RISK",
        "AGENT_TIMEOUT": "NO_NEW_RISK",
        "AGENT_INVALID": "NO_NEW_RISK",
        "AGENT_UNCERTAIN": "NO_NEW_RISK",
    }
)
AGENT_CODES = frozenset({"AGENT_FAILED", "AGENT_TIMEOUT", "AGENT_INVALID", "AGENT_UNCERTAIN"})


@dataclass(frozen=True)
class HealthIncident:
    code: str
    subject: str
    evidence_json: str
    detected_at: datetime
    agent_id: str | None = None
    agent_version: str | None = None
    manifest_ref: str | None = None

    def __post_init__(self):
        _require_aware(self.detected_at)
        if self.detected_at.utcoffset().total_seconds() != 0:
            raise ValueError("Health observation must be UTC")
        if (
            self.code not in FAILURE_STATES
            or not isinstance(self.subject, str)
            or not 1 <= len(self.subject) <= 128
        ):
            raise ValueError("Invalid health failure identity")
        if not isinstance(self.evidence_json, str) or len(self.evidence_json.encode()) > 16384:
            raise ValueError("Health evidence exceeds bound")
        value = json.loads(self.evidence_json)
        if (
            not isinstance(value, dict)
            or json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            != self.evidence_json
        ):
            raise ValueError("Health evidence must be a canonical object")
        pins = (self.agent_id, self.agent_version, self.manifest_ref)
        if self.code in AGENT_CODES:
            if any(not isinstance(pin, str) or not 1 <= len(pin) <= 128 for pin in pins):
                raise ValueError("Agent failure requires exact manifest pins")
        elif any(pin is not None for pin in pins):
            raise ValueError("Non-agent failure cannot quarantine an agent")

    @property
    def incident_id(self):
        identity = [
            self.code,
            self.subject,
            self.evidence_json,
            self.agent_id,
            self.agent_version,
            self.manifest_ref,
        ]
        return sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()

    @property
    def safety_state(self):
        return FAILURE_STATES[self.code]

    @property
    def quarantine(self):
        return self.code in AGENT_CODES
