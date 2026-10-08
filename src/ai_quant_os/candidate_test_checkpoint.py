"""Read source-test custody under its existing lease; never invoke or reconcile workers."""

from hashlib import sha256
from pathlib import Path
import re

from ai_quant_os.candidate_test_checkpoint_contracts import (
    CHECKPOINT_FILES,
    CandidateTestCheckpoint,
    CandidateTestCheckpointFile,
    checkpoint_for_files,
)
from ai_quant_os.candidate_test_custody import IMAGE_ID, LOCK_NAME, CandidateTestJob, restore
from ai_quant_os.candidate_test_execution import (
    _binding,
    _intent,
    _policy,
    _result,
    _root,
    _terminal,
)
from ai_quant_os.repair_containers import _require_owner
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_image_content import parse_metadata


def _snapshot(job: CandidateTestJob) -> dict[str, bytes]:
    retained = {path.name for path in job.root.iterdir()}
    return {
        name: read_repair_image_input(job.root / name, 65536)
        for name, _, _ in CHECKPOINT_FILES
        if name in retained
    }


def _observed(job: CandidateTestJob, snapshot: dict[str, bytes]) -> CandidateTestCheckpoint:
    checkpoint = checkpoint_for_files(frozenset(snapshot))
    values = {name: parse_metadata(raw) for name, raw in snapshot.items()}
    policy = _policy()
    if "create-intent.json" in values and values["create-intent.json"] != _intent(job, policy):
        raise ValueError("Original candidate test create intent changed")
    cid = None
    if "create-ack.json" in values:
        cid = values["create-ack.json"].get("container_id")
        if type(cid) is not str or not re.fullmatch(r"[a-f0-9]{64}", cid):
            raise ValueError("Original candidate test create identity invalid")
        if values["create-ack.json"] != _binding(job, cid):
            raise ValueError("Original candidate test create acknowledgement changed")
    if "created-inspect.json" in values:
        created = values["created-inspect.json"]
        _require_owner(created, owner=job.owner, identity=cid, image=IMAGE_ID)
        policy.require_container(created, owner=job.owner, job=job.path)
    if "start-intent.json" in values and values["start-intent.json"] != {
        **_binding(job, cid),
        "timeout_seconds": 45,
    }:
        raise ValueError("Original candidate test start intent changed")
    if "terminal-inspect.json" in values:
        terminal = values["terminal-inspect.json"]
        _require_owner(terminal, owner=job.owner, identity=cid, image=IMAGE_ID)
        _terminal(terminal)
    for name, expected in (
        ("remove-intent.json", _binding(job, cid)),
        ("removed.json", {**_binding(job, cid), "state": "REMOVED"}),
    ):
        if name in values and values[name] != expected:
            raise ValueError("Original candidate test removal evidence changed")
    result = (
        _result(job, snapshot["worker-result.json"]) if "worker-result.json" in snapshot else None
    )
    if "result.json" in snapshot and _result(job, snapshot["result.json"]) != result:
        raise ValueError("Retained candidate test result differs from original worker output")
    return CandidateTestCheckpoint(
        package_ref=job.request.package_ref,
        candidate_ref=job.request.candidate_ref,
        request_ref=job.request.request_ref,
        manifest_sha256=job.manifest_sha256,
        owner=job.owner,
        container_id=cid,
        checkpoint=checkpoint,
        files=tuple(
            CandidateTestCheckpointFile(name=name, sha256=sha256(raw).hexdigest())
            for name, raw in sorted(snapshot.items())
        ),
        test_status=result.status if result else None,
    )


def capture_candidate_test_checkpoint(root: Path, manifest_sha256: str) -> CandidateTestCheckpoint:
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        job = restore(root, manifest_sha256)
        snapshot = _snapshot(job)
        report = _observed(job, snapshot)
        if _snapshot(restore(root, manifest_sha256)) != snapshot:
            raise ValueError("Candidate worker custody changed during checkpoint observation")
        return report


def verify_candidate_test_checkpoint(
    root: Path,
    manifest_sha256: str,
    report: CandidateTestCheckpoint,
) -> None:
    if type(report) is not CandidateTestCheckpoint or set(report.model_fields_set) - set(
        CandidateTestCheckpoint.model_fields
    ):
        raise ValueError("Exact typed candidate-test checkpoint required")
    report = CandidateTestCheckpoint.model_validate_json(report.model_dump_json())
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        job = restore(root, manifest_sha256)
        snapshot = _snapshot(job)
        _observed(job, snapshot)
        historical = {}
        for retained in report.files:
            raw = snapshot.get(retained.name)
            if raw is None or sha256(raw).hexdigest() != retained.sha256:
                raise ValueError("Original historical worker evidence changed or disappeared")
            historical[retained.name] = raw
        if (
            _observed(job, historical) != report
            or _snapshot(restore(root, manifest_sha256)) != snapshot
        ):
            raise ValueError("Historical candidate-test checkpoint differs from original custody")
