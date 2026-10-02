"""Isolated, pinned Git repair proposals. No execution or release authority."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from types import MappingProxyType

from ai_quant_os.agent_registry import AgentManifest, AgentRegistry
from ai_quant_os.health_contracts import AGENT_CODES, HealthIncident
from ai_quant_os.health_store import HealthStore, encode_incident
from ai_quant_os.repair_snapshots import (
    RepairSourceFile,
    RepairSourceSnapshot,
    SOURCE_PATH,
    canonical_json,
)


# Only the two implemented shadow missions have a declared repair source boundary.
REPAIR_SOURCES = MappingProxyType(
    {
        ("A02", "0.2.0"): frozenset(
            {"src/ai_quant_os/regime_agent.py", "src/ai_quant_os/regime_features.py"}
        ),
        ("A07", "0.2.0"): frozenset({"src/ai_quant_os/integrity_agent.py"}),
    }
)
GATES = ("INDEPENDENT_VERIFICATION", "SHADOW", "CANARY", "RELEASE")
DOSSIER = "repair-candidate.json"
logger = logging.getLogger(__name__)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", value):
        raise ValueError("Invalid repair version")
    return tuple(int(part) for part in value.split("."))


class RepairWorkspace:
    """Local candidate construction; the caller owns incident and registry sources."""

    def __init__(
        self, repository: Path, root: Path, health: HealthStore, registry: AgentRegistry
    ) -> None:
        self.repository = Path(repository).resolve()
        self.root = Path(root).resolve()
        self.health = health
        self.registry = registry
        self._lock = Lock()
        executable = shutil.which("git")
        if executable is None:
            raise ValueError("Git is required for isolated repair candidates")
        self._executable = executable
        if self.repository.is_relative_to(self.root) or (
            self.root.is_relative_to(self.repository)
            and not self.root.is_relative_to(self.repository / "data-local")
        ):
            raise ValueError("Repair storage must be outside tracked source areas")
        top = Path(self._git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if top != self.repository:
            raise ValueError("Repair repository must be its Git root")
        if self._git("rev-parse", "--show-object-format").strip() != b"sha1":
            raise ValueError("Repair Git object format must be sha1")
        self.root.mkdir(parents=True, exist_ok=True)
        self._hooks = self.root / "empty-hooks"
        self._hooks.mkdir(exist_ok=True)

    def _git(self, *arguments: str, content: bytes | None = None, overrides=None) -> bytes:
        # Git plumbing never receives the operator/provider credential environment.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}
        }
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        environment.update(overrides or {})
        hooks = getattr(self, "_hooks", self.root / "empty-hooks")
        result = subprocess.run(
            [
                self._executable,
                "-c",
                f"core.hooksPath={hooks}",
                "-c",
                "core.autocrlf=false",
                "-C",
                str(self.repository),
                *arguments,
            ],
            input=content,
            capture_output=True,
            timeout=30,
            env=environment,
        )
        if result.returncode:
            # Do not expose local Git config, source contents or arbitrary hook output.
            raise ValueError("Repair Git operation failed")
        return result.stdout

    def _path(self, area: str, candidate_id: str, suffix: str = "") -> Path:
        if not isinstance(candidate_id, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_id):
            raise ValueError("Invalid repair candidate identity")
        path = (self.root / area / (candidate_id + suffix)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Repair path escapes storage root")
        return path

    def _incident(self, incident_id: str) -> HealthIncident:
        incident = next(
            (row for row in self.health.incidents() if row.incident_id == incident_id), None
        )
        if incident is None or incident.code not in AGENT_CODES:
            raise ValueError("Repair requires an archived agent failure")
        manifest = self.registry.get(incident.agent_id, incident.agent_version)
        if manifest.manifest_ref != incident.manifest_ref:
            raise ValueError("Repair incident manifest pin mismatch")
        return incident

    def _sources(self, base: str, incident: HealthIncident, replacements: dict) -> list[dict]:
        allowed = REPAIR_SOURCES.get((incident.agent_id, incident.agent_version), frozenset())
        if (
            not isinstance(replacements, dict)
            or not replacements
            or not set(replacements) <= allowed
        ):
            raise ValueError("Repair source outside declared agent boundary")
        sources = []
        for path, text in sorted(replacements.items()):
            if (
                not isinstance(text, str)
                or not 1 <= len(text.encode()) <= 1048576
                or "\x00" in text
            ):
                raise ValueError("Invalid or oversized repair source")
            original = self._source_bytes(base, path)
            before, after = sha256(original).hexdigest(), sha256(text.encode()).hexdigest()
            if before == after:
                raise ValueError("Repair must change each declared source")
            sources.append({"path": path, "before_sha256": before, "after_sha256": after})
        return sources

    def _source_bytes(self, commit: str, path: str) -> bytes:
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("Invalid repair source commit")
        mode = self._git("ls-tree", commit, "--", path).decode().split()
        if len(mode) != 4 or mode[0] != "100644" or mode[1] != "blob":
            raise ValueError("Repair source must be an existing ordinary file")
        raw = self._git("show", f"{commit}:{path}")
        if len(raw) > 1048576:
            raise ValueError("Repair source exceeds bound")
        return raw

    def prepare(
        self,
        incident_id: str,
        *,
        new_version: str,
        replacements: dict[str, str],
        diagnosis: str,
        regression_plan: tuple[str, ...],
    ) -> dict:
        with self._lock:
            incident = self._incident(incident_id)
            if self.registry.lifecycle(incident.agent_id, incident.agent_version) != "QUARANTINED":
                raise ValueError("Repair source requires pinned quarantine")
            original = self.registry.get(incident.agent_id, incident.agent_version)
            if _version(new_version) <= _version(original.agent_version):
                raise ValueError("Repair requires a strictly newer version")
            if any(
                (row.agent_id, row.agent_version) == (incident.agent_id, new_version)
                for row in self.registry.manifests()
            ):
                raise ValueError("Repair version is already registered")
            if not isinstance(diagnosis, str) or not 1 <= len(diagnosis) <= 4096:
                raise ValueError("Repair diagnosis is required and bounded")
            if (
                not isinstance(regression_plan, tuple)
                or not 1 <= len(regression_plan) <= 32
                or any(
                    not isinstance(row, str) or not 1 <= len(row) <= 512 for row in regression_plan
                )
            ):
                raise ValueError("Repair requires a bounded regression plan")
            if self._git("status", "--porcelain", "--untracked-files=no").strip():
                raise ValueError("Repair requires a clean committed baseline")
            base = self._git("rev-parse", "HEAD").decode().strip()
            if self._git("ls-tree", base, "--", DOSSIER).strip():
                raise ValueError("Baseline already contains a repair dossier")
            changes = self._sources(base, incident, replacements)
            proposed = AgentManifest.model_validate(
                {**original.model_dump(), "agent_version": new_version}
            )
            dossier = {
                "schema_version": "1.0",
                "base_commit": base,
                "incident": json.loads(encode_incident(incident)),
                "original_manifest": original.model_dump(mode="json"),
                "proposed_manifest": proposed.model_dump(mode="json"),
                "source_changes": changes,
                "diagnosis": diagnosis,
                "regression_plan": list(regression_plan),
                "state": "AWAITING_INDEPENDENT_VERIFICATION",
                "blocking_gates": list(GATES),
                "promotion_allowed": False,
            }
            raw = _canonical(dossier)
            if len(raw) > 65536:
                raise ValueError("Serialized repair dossier exceeds bound")
            candidate_id = sha256(raw).hexdigest()
            commit = self._commit_candidate(base, candidate_id, incident, replacements, raw)
            branch = f"codex/repair-{candidate_id}"
            existing = self._git(
                "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}"
            ).strip()
            if existing and existing.decode() != commit:
                raise ValueError("Repair branch identity conflict")
            if not existing:
                self._git("update-ref", f"refs/heads/{branch}", commit, "0" * 40)
            receipt = self._receipt(candidate_id, commit, dossier)
            self._publish_receipt(receipt)
            isolated = self._path("workspaces", candidate_id)
            if not isolated.exists():
                isolated.parent.mkdir(parents=True, exist_ok=True)
                self._git("worktree", "add", str(isolated), branch)
            result = self.inspect(candidate_id)
            logger.info(
                "repair_candidate_created candidate_id=%s agent_id=%s",
                candidate_id,
                incident.agent_id,
            )
            return result

    def _commit_candidate(self, base, candidate_id, incident, replacements, raw):
        with tempfile.TemporaryDirectory(dir=self.root, prefix="index-") as temporary:
            overrides = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
            self._git("read-tree", base, overrides=overrides)
            for path, content in [
                *[(path, text.encode()) for path, text in sorted(replacements.items())],
                (DOSSIER, raw),
            ]:
                blob = self._git("hash-object", "-w", "--stdin", content=content).decode().strip()
                self._git(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    blob,
                    path,
                    overrides=overrides,
                )
            tree = self._git("write-tree", overrides=overrides).decode().strip()
            stamp = incident.detected_at.isoformat()
            overrides.update(
                {
                    "GIT_AUTHOR_NAME": "AI Quant Repair Candidate",
                    "GIT_AUTHOR_EMAIL": "repair@local.invalid",
                    "GIT_COMMITTER_NAME": "AI Quant Repair Candidate",
                    "GIT_COMMITTER_EMAIL": "repair@local.invalid",
                    "GIT_AUTHOR_DATE": stamp,
                    "GIT_COMMITTER_DATE": stamp,
                }
            )
            return (
                self._git(
                    "commit-tree",
                    tree,
                    "-p",
                    base,
                    "-m",
                    f"Repair candidate {candidate_id}",
                    overrides=overrides,
                )
                .decode()
                .strip()
            )

    def _receipt(self, candidate_id, commit, dossier):
        return {
            "schema_version": "1.0",
            "candidate_id": candidate_id,
            "candidate_commit": commit,
            "rollback_commit": dossier["base_commit"],
            "branch": f"codex/repair-{candidate_id}",
            "workspace": str(self._path("workspaces", candidate_id)),
            "agent_id": dossier["original_manifest"]["agent_id"],
            "original_version": dossier["original_manifest"]["agent_version"],
            "proposed_version": dossier["proposed_manifest"]["agent_version"],
            "incident_id": self._incident_from_dossier(dossier).incident_id,
            "state": "AWAITING_INDEPENDENT_VERIFICATION",
            "blocking_gates": list(GATES),
            "promotion_allowed": False,
        }

    @staticmethod
    def _incident_from_dossier(dossier):
        data = dict(dossier["incident"])
        data["detected_at"] = datetime.fromisoformat(data["detected_at"])
        return HealthIncident(**data)

    def _publish_receipt(self, receipt):
        path = self._path("receipts", receipt["candidate_id"], ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _canonical(receipt)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("Repair receipt identity conflict") from None
        finally:
            temporary_path.unlink(missing_ok=True)

    def snapshot(self, candidate_id: str) -> RepairSourceSnapshot:
        """Read committed package bytes only; never execute or trust working-tree files."""
        with self._lock:
            receipt = self.inspect(candidate_id)
            commit = receipt["candidate_commit"]
            dossier = json.loads(self._source_bytes(commit, DOSSIER))
            listing = self._git("ls-tree", "-rz", "--full-tree", commit, "--", "src/ai_quant_os")
            if len(listing) > 65536:
                raise ValueError("Repair source inventory exceeds bound")
            entries = [entry for entry in listing.split(b"\x00") if entry]
            if not 1 <= len(entries) <= 256:
                raise ValueError("Repair source inventory exceeds bound")
            files = []
            total = 0
            for entry in entries:
                metadata, path_bytes = entry.split(b"\t", 1)
                path = path_bytes.decode("utf-8")
                if not SOURCE_PATH.fullmatch(path) or not metadata.startswith(b"100644 blob "):
                    raise ValueError("Repair package contains undeclared or irregular source")
                content = self._source_bytes(commit, path)
                total += len(content)
                if total > 16777216:
                    raise ValueError("Repair source inventory exceeds bound")
                files.append(RepairSourceFile(path, content))
            snapshot = RepairSourceSnapshot(
                candidate_id,
                commit,
                receipt["rollback_commit"],
                canonical_json(dossier["proposed_manifest"]),
                canonical_json(dossier["incident"]),
                tuple(sorted(files, key=lambda source: source.path)),
            )
            if self.inspect(candidate_id) != receipt:
                raise ValueError("Repair source changed during snapshot admission")
            return snapshot

    def inspect(self, candidate_id: str) -> dict:
        path = self._path("receipts", candidate_id, ".json")
        raw_receipt = path.read_bytes()
        if len(raw_receipt) > 8192:
            raise ValueError("Repair receipt exceeds bound")
        try:
            receipt = json.loads(raw_receipt)
            commit = receipt["candidate_commit"]
            if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
                raise ValueError("Invalid repair commit")
            raw = self._source_bytes(commit, DOSSIER)
            if len(raw) > 65536 or sha256(raw).hexdigest() != candidate_id:
                raise ValueError("Repair dossier identity mismatch")
            dossier = json.loads(raw)
            if (
                _canonical(dossier) != raw
                or _canonical(self._receipt(candidate_id, commit, dossier)) != raw_receipt
            ):
                raise ValueError("Repair receipt canonical binding failed")
            incident = self._incident_from_dossier(dossier)
            if self._incident(incident.incident_id) != incident:
                raise ValueError("Repair incident source mismatch")
            original = self.registry.get(incident.agent_id, incident.agent_version)
            if dossier["original_manifest"] != original.model_dump(mode="json"):
                raise ValueError("Repair original manifest mismatch")
            proposed = AgentManifest.model_validate(
                {**original.model_dump(), "agent_version": receipt["proposed_version"]}
            )
            if dossier["proposed_manifest"] != proposed.model_dump(mode="json") or _version(
                proposed.agent_version
            ) <= _version(original.agent_version):
                raise ValueError("Repair proposed version mismatch")
            base = dossier["base_commit"]
            if (
                self._git("rev-list", "--parents", "-n", "1", commit).decode().strip()
                != f"{commit} {base}"
            ):
                raise ValueError("Repair rollback parent mismatch")
            paths = {row["path"] for row in dossier["source_changes"]}
            changed = set(
                self._git("diff-tree", "--no-commit-id", "--name-only", "-r", base, commit)
                .decode()
                .splitlines()
            )
            if changed != paths | {DOSSIER}:
                raise ValueError("Repair changed source boundary mismatch")
            allowed = REPAIR_SOURCES.get((incident.agent_id, incident.agent_version), frozenset())
            if not paths or not paths <= allowed:
                raise ValueError("Repair source outside declared agent boundary")
            replacements = {source: self._source_bytes(commit, source).decode() for source in paths}
            if self._sources(base, incident, replacements) != dossier["source_changes"]:
                raise ValueError("Repair source hash binding failed")
        except (KeyError, TypeError, UnicodeError, AttributeError) as error:
            raise ValueError("Repair receipt or dossier contract invalid") from error
        branch = receipt["branch"]
        if (
            self._git("for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}")
            .strip()
            .decode()
            != commit
        ):
            raise ValueError("Repair branch pin changed")
        isolated = self._path("workspaces", candidate_id)
        if not (isolated / ".git").is_file():
            raise ValueError("Repair worktree missing; retry prepare to restore")
        if (
            self._git("-C", str(isolated), "rev-parse", "HEAD").strip().decode() != commit
            or self._git(
                "-C", str(isolated), "status", "--porcelain", "--untracked-files=all"
            ).strip()
        ):
            raise ValueError("Repair worktree differs from pinned candidate")
        return receipt
