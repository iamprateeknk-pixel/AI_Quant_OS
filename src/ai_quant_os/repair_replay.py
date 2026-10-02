"""Verified archival invocation inputs; never restores freshness or invokes an agent."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from ai_quant_os.graph_ledger import GraphLedger, GraphRecord, GraphReceipt
from ai_quant_os.health_contracts import HealthIncident, AGENT_CODES
from ai_quant_os.repair_snapshots import RepairSourceSnapshot, canonical_json


@dataclass(frozen=True)
class RepairIncidentReplay:
    snapshot_sha256: str
    record_json: str

    def __post_init__(self):
        if not isinstance(self.snapshot_sha256, str) or not re.fullmatch(
            r"[a-f0-9]{64}", self.snapshot_sha256
        ):
            raise ValueError("Repair replay source snapshot must be pinned")
        if not isinstance(self.record_json, str) or len(self.record_json.encode()) > 131072:
            raise ValueError("Repair replay record exceeds bound")
        record = GraphRecord.model_validate_json(self.record_json)
        if record.model_dump_json() != self.record_json or record.receipt.status not in {
            "FAILED",
            "TIMEOUT",
            "INVALID",
            "UNCERTAIN",
        }:
            raise ValueError("Repair replay requires a canonical failed invocation")

    @property
    def record(self) -> GraphRecord:
        return GraphRecord.model_validate_json(self.record_json)

    @property
    def record_sha256(self) -> str:
        return sha256(self.record_json.encode()).hexdigest()

    @property
    def replay_sha256(self) -> str:
        return sha256(
            canonical_json(
                {
                    "schema_version": "1.0",
                    "snapshot_sha256": self.snapshot_sha256,
                    "record_sha256": self.record_sha256,
                    "kind": self.kind,
                }
            ).encode()
        ).hexdigest()

    @property
    def kind(self) -> str:
        return "ARCHIVED_INLINE_INPUTS"

    @property
    def promotion_allowed(self) -> bool:
        return False


def load_incident_replay(snapshot: RepairSourceSnapshot, path: Path) -> RepairIncidentReplay:
    """Bind the exact full graph record to the recorded health receipt and old manifest."""
    if type(snapshot) is not RepairSourceSnapshot:
        raise ValueError("Repair replay requires an immutable source snapshot")
    payload = snapshot.incident
    payload["detected_at"] = datetime.fromisoformat(payload["detected_at"])
    incident = HealthIncident(**payload)
    if incident.code not in AGENT_CODES:
        raise ValueError("Repair replay requires an agent incident")
    receipt = GraphReceipt.model_validate_json(incident.evidence_json)
    if (
        canonical_json(json.loads(receipt.model_dump_json())) != incident.evidence_json
        or incident.subject != receipt.run_id
        or incident.code != "AGENT_" + receipt.status
        or (incident.agent_id, incident.agent_version, incident.manifest_ref)
        != (receipt.agent_id, receipt.agent_version, receipt.manifest_ref)
    ):
        raise ValueError("Repair incident receipt binding failed")
    try:
        with GraphLedger.archive(path) as archive:
            record = archive.get(receipt.run_id)
    except (sqlite3.Error, KeyError, ValueError) as error:
        raise ValueError("Repair graph archive integrity or availability failed") from error
    if record.receipt.model_dump_json() != receipt.model_dump_json():
        raise ValueError("Repair incident graph receipt binding failed")
    # The checkpoint already validates every inline source/content reference and expiry.
    # Historical expiry is retained; no current-eligibility or agent invocation runs here.
    return RepairIncidentReplay(snapshot.snapshot_sha256, record.model_dump_json())
