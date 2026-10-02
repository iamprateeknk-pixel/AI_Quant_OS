import json
import os
from pathlib import Path
import stat

import pytest

from ai_quant_os.repair_jobs import PreparedRepairJob, prepared_repair_job
from test_repair_jobs import PROGRAM, SUITE_SHA, snapshot
from test_repair_workspace import repository as repository


def test_staging_receipt_precedes_inputs_and_is_removed_after_normal_cleanup(tmp_path, repository):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        receipt = job.path.parent / (job.path.name + ".stage.json")
        assert receipt.is_file(), "Staging ownership is not retained"
        value = json.loads(receipt.read_bytes())
        assert value["snapshot_sha256"] == job.snapshot_sha256
        assert value["root_inode"] == job._root_identity[1]
    assert not receipt.exists()


def orphan(tmp_path, repository, monkeypatch, partial=False, root=None):
    evidence = snapshot(tmp_path, repository)
    original = PreparedRepairJob.cleanup

    def interrupt(job):
        if partial:
            (job.path / "request.json").unlink()
        raise RuntimeError("injected staging interruption")

    monkeypatch.setattr(PreparedRepairJob, "cleanup", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        with prepared_repair_job(
            evidence,
            PROGRAM,
            suite_sha256=SUITE_SHA,
            root=tmp_path / "jobs" if root is None else root,
        ) as job:
            pass
    monkeypatch.setattr(PreparedRepairJob, "cleanup", original)
    return evidence, job, job.path.parent / (job.path.name + ".stage.json")


@pytest.mark.parametrize("partial", [False, True])
def test_orphan_staging_recovers_against_independent_pins(
    tmp_path, repository, monkeypatch, partial
):
    from ai_quant_os import repair_jobs

    evidence, job, stage = orphan(tmp_path, repository, monkeypatch, partial)
    assert hasattr(repair_jobs, "cleanup_staged_repair_job")
    repair_jobs.cleanup_staged_repair_job(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, root=job.path.parent, stage=stage
    )
    assert not job.path.exists() and not stage.exists()


@pytest.mark.parametrize("fault", ["source", "receipt", "outside", "hardlink"])
def test_staging_tamper_preserves_inputs(tmp_path, repository, monkeypatch, fault):
    from ai_quant_os.repair_jobs import cleanup_staged_repair_job

    evidence, job, stage = orphan(tmp_path, repository, monkeypatch)
    selected = stage
    if fault == "source":
        (job.path / "verifier.py").write_bytes(b"changed")
    elif fault == "receipt":
        stage.write_bytes(b"{}")
    elif fault == "outside":
        selected = tmp_path / stage.name
        selected.write_bytes(stage.read_bytes())
    else:
        os.link(stage, tmp_path / "linked.json")
    with pytest.raises(ValueError):
        cleanup_staged_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=job.path.parent, stage=selected
        )
    assert job.path.exists() and stage.exists()


@pytest.mark.parametrize("missing_lease", [False, True])
def test_staging_cleanup_never_bypasses_pending_containers(
    tmp_path, repository, monkeypatch, missing_lease
):
    from ai_quant_os.repair_jobs import cleanup_staged_repair_job
    from ai_quant_os.repair_locks import repair_root_lock
    from ai_quant_os.repair_removals import RemovalJournal
    from test_repair_sandbox import IMAGE

    evidence, job, stage = orphan(tmp_path, repository, monkeypatch)
    with repair_root_lock(job.path.parent):
        owner = "a" * 32
        job.hold_container(owner, image_id=IMAGE)
        with RemovalJournal(job.path.parent) as journal:
            journal.create_intent(job, owner, image_id=IMAGE, arguments=("container", "create"))
        if missing_lease:
            job._lease(owner, IMAGE)[0].unlink()
    with pytest.raises(ValueError, match="unresolved|unfinished"):
        cleanup_staged_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=job.path.parent, stage=stage
        )
    assert job.path.exists() and stage.exists()


def test_stage_record_exists_before_first_source_write(tmp_path, repository, monkeypatch):
    evidence = snapshot(tmp_path, repository)
    original = Path.open
    observed = []

    def opening(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.parent.name == "ai_quant_os":
            root = path.parents[2]
            assert (root.parent / (root.name + ".stage.json")).is_file()
            observed.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opening)
    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"):
        pass
    assert observed


def test_new_cli_process_cleans_orphan_staging_without_source_output(
    tmp_path, repository, monkeypatch
):
    from test_repair_cli import cli

    evidence, job, stage = orphan(tmp_path, repository, monkeypatch, root=tmp_path / "repairs/jobs")
    suite = tmp_path / "suite.py"
    suite.write_bytes(PROGRAM)
    result = cli(
        repository,
        tmp_path,
        "cleanup-staging",
        evidence.candidate_id,
        "--stage",
        str(stage),
        "--suite-file",
        str(suite),
        "--suite-sha256",
        SUITE_SHA,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "STAGING_REMOVED"
    assert "VERIFIER_MUST_NOT_RUN" not in result.stdout
    assert not job.path.exists() and not stage.exists()


def test_staging_explicitly_sets_readable_modes_for_all_mounted_inputs(
    tmp_path, repository, monkeypatch
):
    evidence = snapshot(tmp_path, repository)
    original = Path.chmod
    calls = {}

    def chmod(path, mode, *args, **kwargs):
        calls[path] = mode
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)
    with prepared_repair_job(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        assert calls.get(job.path) == 0o755
        assert calls.get(job.path / "src") == 0o755
        assert calls.get(job.path / "src/ai_quant_os") == 0o755
        assert all(calls.get(job.path / relative) == 0o644 for relative, _ in job._expected_files)


@pytest.mark.skipif(os.name != "posix", reason="Requires real POSIX permission semantics")
def test_restrictive_umask_preserves_private_control_and_readable_input_modes(tmp_path, repository):
    evidence = snapshot(tmp_path, repository)
    prior = os.umask(0o077)
    try:
        with prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            assert stat.S_IMODE(job.path.stat().st_mode) == 0o755
            for relative, _ in job._expected_files:
                assert stat.S_IMODE((job.path / relative).stat().st_mode) == 0o644
            assert stat.S_IMODE((job.path / "src").stat().st_mode) == 0o755
            assert stat.S_IMODE((job.path / "src/ai_quant_os").stat().st_mode) == 0o755
            assert stat.S_IMODE(job._stage()[0].stat().st_mode) == 0o600
    finally:
        os.umask(prior)


@pytest.mark.skipif(os.name != "posix", reason="Requires real POSIX permission semantics")
@pytest.mark.parametrize("target", ["root", "directory", "file"])
def test_unreadable_inputs_fail_admission_before_container_access(tmp_path, repository, target):
    evidence = snapshot(tmp_path, repository)
    with prepared_repair_job(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        selected = (
            job.path
            if target == "root"
            else job.path / "src"
            if target == "directory"
            else job.path / "verifier.py"
        )
        original = stat.S_IMODE(selected.stat().st_mode)
        selected.chmod(0o700 if selected.is_dir() else 0o600)
        try:
            with pytest.raises(ValueError, match="permission"):
                job.verify()
        finally:
            selected.chmod(original)
