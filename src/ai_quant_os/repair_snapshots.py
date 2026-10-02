"""Immutable, complete package bytes and source-bound repair verification identity."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256


SOURCE_PATH = re.compile(r"src/ai_quant_os/(?:[a-z_][a-z0-9_]*\.py|strategy_manifests\.json)")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class RepairSourceFile:
    path: str
    content: bytes

    def __post_init__(self):
        if (
            not isinstance(self.path, str)
            or not SOURCE_PATH.fullmatch(self.path)
            or not isinstance(self.content, bytes)
            or len(self.content) > 1048576
        ):
            raise ValueError("Invalid bounded repair source snapshot")

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()


@dataclass(frozen=True)
class RepairSourceSnapshot:
    candidate_id: str
    candidate_commit: str
    rollback_commit: str
    manifest_json: str
    incident_json: str
    files: tuple[RepairSourceFile, ...]

    def __post_init__(self):
        for value, size in (
            (self.candidate_id, 64),
            (self.candidate_commit, 40),
            (self.rollback_commit, 40),
        ):
            if not isinstance(value, str) or not re.fullmatch(rf"[a-f0-9]{{{size}}}", value):
                raise ValueError("Repair source identity must be pinned")
        for raw in (self.manifest_json, self.incident_json):
            if not isinstance(raw, str) or len(raw.encode()) > 65536:
                raise ValueError("Repair source metadata exceeds bound")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict) or canonical_json(decoded) != raw:
                raise ValueError("Repair source metadata must be canonical")
        if (
            not isinstance(self.files, tuple)
            or not 1 <= len(self.files) <= 256
            or any(type(source) is not RepairSourceFile for source in self.files)
            or sum(len(source.content) for source in self.files) > 16777216
        ):
            raise ValueError("Repair source inventory exceeds bound")
        paths = tuple(source.path for source in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("Repair source inventory must be unique and sorted")

    @property
    def proposed_manifest(self) -> dict:
        return json.loads(self.manifest_json)

    @property
    def incident(self) -> dict:
        return json.loads(self.incident_json)

    @property
    def inventory(self) -> list[dict]:
        return [
            {"path": source.path, "sha256": source.sha256, "bytes": len(source.content)}
            for source in self.files
        ]

    @property
    def snapshot_sha256(self) -> str:
        identity = {
            "schema_version": "1.0",
            "candidate_id": self.candidate_id,
            "candidate_commit": self.candidate_commit,
            "rollback_commit": self.rollback_commit,
            "proposed_manifest": self.proposed_manifest,
            "incident": self.incident,
            "inventory": self.inventory,
        }
        return sha256(canonical_json(identity).encode()).hexdigest()

    @property
    def promotion_allowed(self) -> bool:
        return False
