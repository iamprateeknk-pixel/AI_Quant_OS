import importlib
import json
import os
from hashlib import sha256

import pytest

from test_repair_workspace import SOURCE, REPLACEMENT, prepare, workspace
from test_repair_workspace import repository as repository


PROGRAM = b'raise RuntimeError("VERIFIER_MUST_NOT_RUN_ON_HOST")\n'
SUITE_SHA = sha256(PROGRAM).hexdigest()


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_jobs") is not None, (
        "Missing disposable repair verification job"
    )
    return importlib.import_module("ai_quant_os.repair_jobs")


def snapshot(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    return value.snapshot(candidate["candidate_id"])


def test_job_contains_exact_committed_sources_and_owned_suite_without_execution(
    tmp_path, repository
):
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs"
    with module().prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
        path = job.path
        assert path.is_relative_to(root)
        assert (path / SOURCE).read_bytes() == REPLACEMENT.encode()
        assert (path / "src/ai_quant_os/risk.py").read_bytes() == b"LIMIT = 100\n"
        assert (path / "verifier.py").read_bytes() == PROGRAM
        request = json.loads((path / "request.json").read_bytes())
        assert request["snapshot_sha256"] == evidence.snapshot_sha256
        assert request["suite_sha256"] == SUITE_SHA
        assert request["manifest"]["agent_version"] == "0.2.1"
        assert request["scope"] == "SANDBOX"
        assert request["promotion_allowed"] is False
        assert len(request["inventory"]) == 2
        assert job.promotion_allowed is False
        job.verify()
    assert not path.exists()


def test_mismatched_owned_suite_is_rejected_before_writing_job(tmp_path, repository):
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs"
    with pytest.raises(ValueError, match="suite"):
        with module().prepared_repair_job(evidence, PROGRAM, suite_sha256="0" * 64, root=root):
            pytest.fail("Unpinned suite admitted")
    assert not root.exists()


def test_changed_source_or_extra_file_cannot_pass_job_admission(tmp_path, repository):
    evidence = snapshot(tmp_path, repository)
    with module().prepared_repair_job(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        (job.path / SOURCE).write_bytes(b"changed = True\n")
        with pytest.raises(ValueError, match="job"):
            job.verify()
        (job.path / SOURCE).write_bytes(REPLACEMENT.encode())
        (job.path / "secret.env").write_bytes(b"fixture-only")
        with pytest.raises(ValueError, match="job"):
            job.verify()


def test_hard_linked_job_file_is_denied_and_cleanup_preserves_outside_link(tmp_path, repository):
    evidence = snapshot(tmp_path, repository)
    external = tmp_path / "external.py"
    with module().prepared_repair_job(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        os.link(job.path / SOURCE, external)
        with pytest.raises(ValueError, match="job"):
            job.verify()
    assert external.read_bytes() == REPLACEMENT.encode()


def test_job_cleanup_runs_after_consumer_failure(tmp_path, repository):
    evidence = snapshot(tmp_path, repository)
    with pytest.raises(RuntimeError, match="consumer"):
        with module().prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            path = job.path
            raise RuntimeError("injected consumer failure")
    assert not path.exists()
