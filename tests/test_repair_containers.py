"""Fault-injected Docker lifecycle tests; no container or candidate is run."""

import copy
import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_quant_os.repair_jobs import prepared_repair_job
from ai_quant_os.repair_sandbox import DockerConfinement, SandboxProbe
from ai_quant_os.repair_transport import CommandResult
from test_repair_jobs import PROGRAM, SUITE_SHA, snapshot
from test_repair_workspace import repository as repository
from test_repair_sandbox import IMAGE, container

CID = "c" * 64


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_containers") is not None
    return importlib.import_module("ai_quant_os.repair_containers")


class DockerFixture:
    def __init__(
        self,
        monkeypatch,
        job,
        *,
        create_error=False,
        weaken=False,
        cleanup_error=False,
        foreign=False,
        missing=False,
    ):
        self.calls = []
        self.owner = None
        self.job = job
        self.create_error, self.weaken, self.cleanup_error, self.foreign, self.missing = (
            create_error,
            weaken,
            cleanup_error,
            foreign,
            missing,
        )
        original_which = shutil.which
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "owned-docker-fixture" if name == "docker" else original_which(name),
        )
        monkeypatch.setattr(
            DockerConfinement,
            "probe",
            lambda policy: SandboxProbe(True, "PREREQUISITES_PRESENT", IMAGE, SUITE_SHA),
        )
        monkeypatch.setattr(DockerConfinement, "_command", self.command)

    def command(self, executable, *arguments):
        self.calls.append(arguments)
        assert executable == "owned-docker-fixture"
        if arguments[:2] == ("container", "create"):
            mount = arguments[arguments.index("--mount") + 1]
            self.job = SimpleNamespace(
                path=Path(
                    mount.removeprefix("type=bind,source=").removesuffix(",target=/job,readonly")
                )
            )
            self.owner = arguments[arguments.index("--name") + 1].removeprefix("ai-quant-repair-")
            if self.create_error:
                raise TimeoutError("ambiguous create fixture")
            return CommandResult(0, (CID + "\n").encode(), b"")
        if arguments[:2] == ("container", "inspect"):
            if self.missing:
                return CommandResult(1, b"", b"not found fixture")
            value = copy.deepcopy(container(self.job.path))
            value.update(Id=CID, Name="/ai-quant-repair-" + self.owner)
            value["Config"]["Labels"]["ai-quant.repair-owner"] = (
                "foreign" if self.foreign else self.owner
            )
            if self.weaken:
                value["HostConfig"]["ReadonlyRootfs"] = False
            return CommandResult(0, json.dumps(value).encode(), b"")
        if arguments[:2] == ("container", "rm"):
            assert arguments == ("container", "rm", "--force", "--volumes", CID)
            if self.cleanup_error:
                raise TimeoutError("ambiguous removal fixture")
            return CommandResult(0, (CID + "\n").encode(), b"")
        pytest.fail("Unexpected operation: " + repr(arguments))


def test_created_container_is_admitted_without_start_and_removed_before_job_cleanup(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        with module().prepared_repair_container(
            DockerConfinement(IMAGE, SUITE_SHA), job
        ) as created:
            assert created.container_id == CID
            assert created.promotion_allowed is False
            assert job.path.exists()
            assert job.pending_containers == (fixture.owner,)
            create = fixture.calls[0]
            assert create[create.index("--pull") + 1] == "never"
            assert create[create.index("--read-only")] == "--read-only"
            assert create[create.index("--network") + 1] == "none"
            assert create[create.index("--mount") + 1].endswith(",target=/job,readonly")
            assert create[create.index("--no-healthcheck")] == "--no-healthcheck"
        assert job.pending_containers == ()
        assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)
        path = job.path
    assert not path.exists()
    assert not list((tmp_path / "jobs").glob("*.lease.json"))
    assert all("start" not in command and "run" not in command for command in fixture.calls)


def test_full_journal_refuses_new_container_before_daemon_calls_or_lease_publication(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal

    original = RemovalJournal.__init__

    def limited(journal, root, **kwargs):
        original(journal, root, capacity=1)

    monkeypatch.setattr(RemovalJournal, "__init__", limited)
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs"
    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
        DockerFixture(monkeypatch, job)
        with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
            pass
    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
        fixture = DockerFixture(monkeypatch, job)
        with pytest.raises(ValueError, match="capacity"):
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("A full journal allowed creation")
        assert fixture.calls == []
        assert not job.pending_containers
        assert not list(root.glob("*.lease.json"))


def test_consumer_failure_still_removes_owned_container_first(tmp_path, repository, monkeypatch):
    with pytest.raises(RuntimeError, match="consumer"):
        with prepared_repair_job(
            snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                path = job.path
                raise RuntimeError("consumer failure")
    assert not path.exists()
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)


def test_ambiguous_create_adopts_only_matching_owned_container_for_cleanup(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job, create_error=True)
        with pytest.raises(TimeoutError, match="create"):
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("Ambiguous create admitted")
        assert job.pending_containers == ()
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)


def test_weakened_created_container_is_rejected_and_owned_object_removed(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job, weaken=True)
        with pytest.raises(ValueError, match="confinement"):
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("Weakened container admitted")
        assert job.pending_containers == ()
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)


@pytest.mark.parametrize("fault", ["cleanup_error", "foreign", "missing", "ambiguous_missing"])
def test_unconfirmed_cleanup_preserves_job_and_owner_receipt_without_deleting_foreign_object(
    tmp_path, repository, monkeypatch, fault
):
    evidence = snapshot(tmp_path, repository)
    with pytest.raises(RuntimeError, match="retained"):
        with prepared_repair_job(
            evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            options = (
                {"create_error": True, "missing": True}
                if fault == "ambiguous_missing"
                else {fault: True}
            )
            fixture = DockerFixture(monkeypatch, job, **options)
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pass
    assert job.path.exists()
    assert (job.path / "verifier.py").read_bytes() == PROGRAM
    assert job.pending_containers == (fixture.owner,)
    receipts = list((tmp_path / "jobs").glob("*.lease.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_bytes())["owner"] == fixture.owner
    assert json.loads(receipts[0].read_bytes())["image_id"] == IMAGE
    with pytest.raises(ValueError, match="unresolved"):
        with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"):
            pytest.fail("Retry staged another job beside an unresolved lease")
    if fault != "cleanup_error":
        assert all(command[:2] != ("container", "rm") for command in fixture.calls)


def test_unavailable_runtime_or_wrong_suite_never_creates_or_leases(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        with pytest.raises(ValueError, match="suite"):
            with module().prepared_repair_container(DockerConfinement(IMAGE, "d" * 64), job):
                pytest.fail("Wrong suite admitted")
        monkeypatch.setattr(
            DockerConfinement,
            "probe",
            lambda policy: SandboxProbe(False, "ENGINE_UNAVAILABLE", IMAGE, SUITE_SHA),
        )
        with pytest.raises(ValueError, match="prerequisites"):
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("Unavailable runtime admitted")
        assert job.pending_containers == ()
        assert fixture.calls == []


def test_installed_cli_boundary_admits_and_reports_only_after_confirmed_cleanup(
    tmp_path, repository, monkeypatch, capsys
):
    from ai_quant_os.repair_cli import main

    evidence = snapshot(tmp_path, repository)
    suite = tmp_path / "owned-suite.py"
    suite.write_bytes(PROGRAM)
    fixture = DockerFixture(monkeypatch, SimpleNamespace(path=tmp_path))
    result = main(
        [
            "--repository",
            str(repository),
            "--root",
            str(tmp_path / "repairs"),
            "--health-store",
            str(tmp_path / "health.sqlite3"),
            "admit",
            evidence.candidate_id,
            "--suite-file",
            str(suite),
            "--image-id",
            IMAGE,
            "--suite-sha256",
            SUITE_SHA,
        ]
    )
    assert result == 0
    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert receipt["state"] == "REMOVED_AFTER_ADMISSION"
    assert receipt["container_id"] == CID
    assert receipt["promotion_allowed"] is False
    assert receipt["execution_verified"] is False
    assert "VERIFIER_MUST_NOT_RUN_ON_HOST" not in output
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)
    assert not list((tmp_path / "repairs" / "jobs").glob("repair-job-*"))


def test_second_container_cannot_take_an_existing_job_lease(tmp_path, repository, monkeypatch):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        policy = DockerConfinement(IMAGE, SUITE_SHA)
        with module().prepared_repair_container(policy, job):
            calls_before = len(fixture.calls)
            with pytest.raises(ValueError, match="pending container"):
                with module().prepared_repair_container(policy, job):
                    pytest.fail("Concurrent owner admitted")
            assert len(fixture.calls) == calls_before


def test_real_cli_missing_pinned_image_never_creates_container_or_leaves_job(tmp_path, repository):
    from test_repair_cli import cli

    evidence = snapshot(tmp_path, repository)
    suite = tmp_path / "owned-suite.py"
    suite.write_bytes(PROGRAM)
    result = cli(
        repository,
        tmp_path,
        "admit",
        evidence.candidate_id,
        "--suite-file",
        str(suite),
        "--suite-sha256",
        SUITE_SHA,
        "--image-id",
        IMAGE,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert not list((tmp_path / "repairs" / "jobs").glob("repair-job-*"))
