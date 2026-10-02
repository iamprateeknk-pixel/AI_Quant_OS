"""Restart cleanup uses real retained jobs and a fault-injected Docker boundary."""

import importlib
import json
import os

import pytest

from ai_quant_os.repair_jobs import prepared_repair_job
from ai_quant_os.repair_sandbox import DockerConfinement
from test_repair_containers import DockerFixture, CID, module as containers
from test_repair_jobs import PROGRAM, SUITE_SHA, snapshot
from test_repair_sandbox import IMAGE
from test_repair_workspace import repository as repository


def loader():
    module = importlib.import_module("ai_quant_os.repair_jobs")
    assert hasattr(module, "load_retained_repair_job"), "Missing retained job reconstruction"
    return module.load_retained_repair_job


def retained(tmp_path, repository, monkeypatch, root=None):
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs" if root is None else root
    with pytest.raises(RuntimeError, match="retained"):
        with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
            fixture = DockerFixture(monkeypatch, job, cleanup_error=True)
            with containers().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pass
    lease = next(root.glob("*.lease.json"))
    return evidence, job.path, lease, fixture


def read(evidence, lease, root):
    return loader()(
        evidence, PROGRAM, suite_sha256=SUITE_SHA, image_id=IMAGE, root=root, lease=lease
    )


def test_reconstructs_exact_retained_inputs_without_changing_files_and_cleans_present_owned_container(
    tmp_path, repository, monkeypatch
):
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    before = (path / "request.json").read_bytes(), lease.read_bytes()
    job = read(evidence, lease, tmp_path / "jobs")
    assert job.path == path
    assert job.pending_containers == (fixture.owner,)
    assert ((path / "request.json").read_bytes(), lease.read_bytes()) == before
    fixture.cleanup_error = False
    fixture.calls.clear()
    receipt = containers().cleanup_retained_repair_container(
        DockerConfinement(IMAGE, SUITE_SHA), job
    )
    assert receipt["container_id"] == CID
    assert receipt["state"] == "REMOVED_AFTER_RESTART_RECONCILIATION"
    assert receipt["execution_verified"] is False
    assert receipt["promotion_allowed"] is False
    assert not path.exists()
    assert not lease.exists()
    assert fixture.calls[0][2] == "ai-quant-repair-" + fixture.owner
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)


@pytest.mark.parametrize("fault", ["missing", "foreign", "cleanup_error"])
def test_restart_unknown_absence_or_ownership_or_removal_never_deletes_inputs(
    tmp_path, repository, monkeypatch, fault
):
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    fixture.cleanup_error = False
    setattr(fixture, fault, True)
    fixture.calls.clear()
    job = read(evidence, lease, tmp_path / "jobs")
    with pytest.raises((ValueError, OSError, RuntimeError)):
        containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job)
    assert path.exists() and lease.exists()
    assert job.pending_containers == (fixture.owner,)
    if fault != "cleanup_error":
        assert all(command[:2] != ("container", "rm") for command in fixture.calls)


@pytest.mark.parametrize(
    "fault", ["source", "request", "image", "root_identity", "extra_authority", "format"]
)
def test_tampered_retained_inputs_or_receipt_are_denied_before_daemon_access(
    tmp_path, repository, monkeypatch, fault
):
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    fixture.calls.clear()
    if fault == "source":
        (path / "src/ai_quant_os/risk.py").write_bytes(b"LIMIT = 0\n")
    elif fault == "request":
        (path / "request.json").write_text("{}", encoding="utf-8")
    else:
        value = json.loads(lease.read_bytes())
        if fault == "image":
            value["image_id"] = "sha256:" + "f" * 64
        elif fault == "root_identity":
            value["root_inode"] += 1
        elif fault == "extra_authority":
            value["promotion_allowed"] = True
        from ai_quant_os.repair_snapshots import canonical_json

        lease.write_text(
            json.dumps(value, indent=2) if fault == "format" else canonical_json(value),
            encoding="utf-8",
        )
    with pytest.raises(ValueError):
        read(evidence, lease, tmp_path / "jobs")
    assert fixture.calls == []
    assert path.exists() and lease.exists()


def test_missing_root_and_hard_linked_or_outside_receipt_are_never_adopted(
    tmp_path, repository, monkeypatch
):
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    missing = tmp_path / "missing"
    with pytest.raises(ValueError):
        read(evidence, lease, missing)
    assert not missing.exists()
    external = tmp_path / lease.name
    os.link(lease, external)
    for selected in [lease, external]:
        with pytest.raises(ValueError):
            read(evidence, selected, tmp_path / "jobs")
    assert path.exists() and external.exists()


def test_receipt_changed_after_reconstruction_is_denied_before_removal(
    tmp_path, repository, monkeypatch
):
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    job = read(evidence, lease, tmp_path / "jobs")
    lease.write_text("{}", encoding="utf-8")
    fixture.cleanup_error = False
    fixture.calls.clear()
    with pytest.raises(ValueError):
        containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job)
    assert fixture.calls == []
    assert path.exists() and lease.exists()


def test_cli_reconstructs_retained_job_and_reports_only_after_cleanup(
    tmp_path, repository, monkeypatch, capsys
):
    from ai_quant_os.repair_cli import main

    root = tmp_path / "repairs" / "jobs"
    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch, root)
    fixture.cleanup_error = False
    suite = tmp_path / "suite.py"
    suite.write_bytes(PROGRAM)
    result = main(
        [
            "--repository",
            str(repository),
            "--root",
            str(tmp_path / "repairs"),
            "--health-store",
            str(tmp_path / "health.sqlite3"),
            "cleanup",
            evidence.candidate_id,
            "--lease",
            str(lease),
            "--suite-file",
            str(suite),
            "--suite-sha256",
            SUITE_SHA,
            "--image-id",
            IMAGE,
        ]
    )
    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["state"] == "REMOVED_AFTER_RESTART_RECONCILIATION"
    assert receipt["candidate_id"] == evidence.candidate_id
    assert not path.exists() and not lease.exists()


def test_new_cli_process_without_local_daemon_preserves_reconstructed_job(
    tmp_path, repository, monkeypatch
):
    from test_repair_cli import cli

    evidence, path, lease, fixture = retained(
        tmp_path, repository, monkeypatch, tmp_path / "repairs" / "jobs"
    )
    suite = tmp_path / "suite.py"
    suite.write_bytes(PROGRAM)
    result = cli(
        repository,
        tmp_path,
        "cleanup",
        evidence.candidate_id,
        "--lease",
        str(lease),
        "--suite-file",
        str(suite),
        "--suite-sha256",
        SUITE_SHA,
        "--image-id",
        IMAGE,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert path.exists() and lease.exists()


def test_confirmed_removal_survives_interrupted_lease_release_and_resumes_without_daemon(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_jobs import PreparedRepairJob
    from test_repair_jobs import snapshot as make_snapshot
    from ai_quant_os.repair_jobs import prepared_repair_job
    from test_repair_containers import DockerFixture

    evidence = make_snapshot(tmp_path, repository)
    original = PreparedRepairJob.release_container

    def fail_release(*args):
        raise RuntimeError("injected lease release interruption")

    monkeypatch.setattr(PreparedRepairJob, "release_container", fail_release)
    with pytest.raises(RuntimeError, match="retained"):
        with prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with containers().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pass
    lease = next((tmp_path / "jobs").glob("*.lease.json"))
    monkeypatch.setattr(PreparedRepairJob, "release_container", original)
    fixture.missing = True
    fixture.calls.clear()
    rebuilt = read(evidence, lease, tmp_path / "jobs")
    receipt = containers().cleanup_retained_repair_container(
        DockerConfinement(IMAGE, SUITE_SHA), rebuilt
    )
    assert receipt["container_id"] == CID
    assert fixture.calls == []
    assert not job.path.exists() and not lease.exists()


def test_missing_ack_commit_never_turns_absence_into_removal_confirmation(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal
    from test_repair_containers import DockerFixture

    evidence = snapshot(tmp_path, repository)
    original = RemovalJournal.removed

    def fail_ack(*args):
        raise ValueError("injected acknowledgement persistence failure")

    monkeypatch.setattr(RemovalJournal, "removed", fail_ack)
    with pytest.raises(RuntimeError, match="retained"):
        with prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with containers().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pass
    lease = next((tmp_path / "jobs").glob("*.lease.json"))
    monkeypatch.setattr(RemovalJournal, "removed", original)
    fixture.missing = True
    rebuilt = read(evidence, lease, tmp_path / "jobs")
    with pytest.raises(ValueError):
        containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), rebuilt)
    assert job.path.exists() and lease.exists()


def interrupted_cleanup(tmp_path, repository, monkeypatch, phase):
    import shutil
    from ai_quant_os.repair_jobs import PreparedRepairJob

    evidence = snapshot(tmp_path, repository)
    original = PreparedRepairJob.cleanup

    def interrupt(job):
        if phase == "partial":
            (job.path / "request.json").unlink()
        elif phase == "deleted":
            shutil.rmtree(job.path)
        raise RuntimeError("injected input cleanup interruption")

    monkeypatch.setattr(PreparedRepairJob, "cleanup", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        with prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with containers().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pass
    lease = job.path.parent / f"{job.path.name}.{fixture.owner}.lease.json"
    assert not lease.exists()
    monkeypatch.setattr(PreparedRepairJob, "cleanup", original)
    fixture.missing = True
    fixture.calls.clear()
    return evidence, job.path, lease, fixture


@pytest.mark.parametrize("phase", ["released", "partial", "deleted"])
def test_interrupted_input_cleanup_recovers_from_exact_removal_without_lease_or_daemon(
    tmp_path, repository, monkeypatch, phase
):
    evidence, path, lease, fixture = interrupted_cleanup(tmp_path, repository, monkeypatch, phase)
    rebuilt = read(evidence, lease, tmp_path / "jobs")
    receipt = containers().cleanup_retained_repair_container(
        DockerConfinement(IMAGE, SUITE_SHA), rebuilt
    )
    assert receipt["container_id"] == CID
    assert fixture.calls == []
    assert not path.exists() and not lease.exists()
    # Delivery after terminal cleanup is safe and does not touch the daemon.
    rebuilt = read(evidence, lease, tmp_path / "jobs")
    assert (
        containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), rebuilt)
        == receipt
    )
    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"):
        pass


@pytest.mark.parametrize("fault", ["bytes", "file", "directory", "hardlink", "root", "receipt"])
def test_confirmed_removal_never_authorizes_deletion_of_changed_surviving_inputs(
    tmp_path, repository, monkeypatch, fault
):
    evidence, path, lease, fixture = interrupted_cleanup(
        tmp_path, repository, monkeypatch, "partial"
    )
    if fault == "bytes":
        (path / "src/ai_quant_os/risk.py").write_bytes(b"LIMIT = 200\n")
    elif fault == "file":
        (path / "unknown.txt").write_bytes(b"preserve")
    elif fault == "directory":
        (path / "unexpected").mkdir()
    elif fault == "hardlink":
        os.link(path / "verifier.py", tmp_path / "external.py")
    elif fault == "root":
        path.rename(path.parent / "kept-original")
        path.mkdir()
    else:
        lease.write_bytes(b"{}")
    with pytest.raises(ValueError):
        read(evidence, lease, tmp_path / "jobs")
    assert path.exists()
    assert fixture.calls == []


def test_terminal_commit_failure_blocks_staging_until_absent_job_is_reconciled(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal

    evidence, path, lease, fixture = interrupted_cleanup(
        tmp_path, repository, monkeypatch, "released"
    )
    original = RemovalJournal.cleaned

    def fail_terminal(*args, **kwargs):
        raise RuntimeError("injected terminal commit failure")

    monkeypatch.setattr(RemovalJournal, "cleaned", fail_terminal)
    job = read(evidence, lease, tmp_path / "jobs")
    with pytest.raises(RuntimeError, match="terminal commit"):
        containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job)
    assert not path.exists() and not lease.exists()
    with pytest.raises(ValueError, match="unfinished"):
        with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"):
            pytest.fail("Uncommitted terminal cleanup allowed new staging")
    monkeypatch.setattr(RemovalJournal, "cleaned", original)
    job = read(evidence, lease, tmp_path / "jobs")
    containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job)
    assert fixture.calls == []
    with RemovalJournal(tmp_path / "jobs") as journal:
        assert not journal.unfinished
        assert journal.event_count == 3
