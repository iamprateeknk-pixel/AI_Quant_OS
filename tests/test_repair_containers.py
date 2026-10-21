"""Fault-injected lifecycle tests plus opt-in pinned-engine smoke tests.

The live smoke tests exercise a harmless fixed program; they do not run a
repair candidate.
"""

import copy
import importlib
import json
import os
import shutil
from hashlib import sha256
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
        self.started = False
        self.terminal_exit_code = 0
        self.oom_killed = False
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
            value["RestartCount"] = 0
            if self.started:
                value["State"] = {
                    "Status": "exited",
                    "Running": False,
                    "Paused": False,
                    "Restarting": False,
                    "Dead": False,
                    "OOMKilled": self.oom_killed,
                    "Pid": 0,
                    "ExitCode": self.terminal_exit_code,
                    "Error": "",
                }
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


def test_create_rpc_is_preceded_by_durable_exact_request_and_ack_survives_restart(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal

    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        command = fixture.command

        def observed(executable, *arguments):
            if arguments[:2] == ("container", "create"):
                owner = arguments[arguments.index("--name") + 1].removeprefix("ai-quant-repair-")
                with RemovalJournal(job.path.parent) as journal:
                    assert journal.event_count == 1, "Create request was not committed before RPC"
                    journal.require_create_request(job, owner, image_id=IMAGE, arguments=arguments)
                job.verify_container_lease(owner)
            return command(executable, *arguments)

        monkeypatch.setattr(DockerConfinement, "_command", staticmethod(observed))
        with module().prepared_repair_container(
            DockerConfinement(IMAGE, SUITE_SHA), job
        ) as created:
            with RemovalJournal(job.path.parent) as journal:
                assert journal.event_count == 2
                assert journal.created_identity(job, created.owner, image_id=IMAGE) == CID
        path = job.path
    with RemovalJournal(path.parent) as journal:
        assert journal.event_count == 5
        assert not journal.unfinished


def test_failed_create_intent_commit_makes_no_daemon_call_and_preserves_lease(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal

    def fail_intent(*args, **kwargs):
        raise ValueError("injected create intent commit failure")

    monkeypatch.setattr(RemovalJournal, "create_intent", fail_intent)
    with pytest.raises(RuntimeError, match="retained"):
        with prepared_repair_job(
            snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("Uncommitted creation was admitted")
    assert fixture.calls == []
    assert job.path.exists() and len(list(job.path.parent.glob("*.lease.json"))) == 1


def test_failed_create_ack_commit_reconciles_for_cleanup_without_admission_or_resend(
    tmp_path, repository, monkeypatch
):
    from ai_quant_os.repair_removals import RemovalJournal

    def fail_ack(*args, **kwargs):
        raise ValueError("injected create acknowledgement commit failure")

    monkeypatch.setattr(RemovalJournal, "create_ack", fail_ack)
    with pytest.raises(ValueError, match="acknowledgement commit"):
        with prepared_repair_job(
            snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
        ) as job:
            fixture = DockerFixture(monkeypatch, job)
            with module().prepared_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job):
                pytest.fail("Uncommitted creation acknowledgement was admitted")
    assert not job.path.exists()
    assert sum(command[:2] == ("container", "create") for command in fixture.calls) == 1
    with RemovalJournal(job.path.parent) as journal:
        assert journal.event_count == 4
        assert not journal.unfinished


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


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_REPAIR_CONTAINER_ENGINE_TEST") != "1",
    reason="Explicit real-engine repair-container admission gate required",
)
def test_actual_repair_container_is_admitted_then_removed_without_start(tmp_path, repository):
    from ai_quant_os.repair_containers import _inspect, _require_owner
    from ai_quant_os.repair_sandbox import local_docker_command

    image = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"
    policy = DockerConfinement(image, SUITE_SHA)
    assert policy.probe().available
    evidence = snapshot(tmp_path, repository)
    report = tmp_path / "actual-repair-container.json"
    root = tmp_path / "jobs"

    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
        with module().prepared_repair_container(policy, job) as created:
            metadata = _inspect(policy, shutil.which("docker"), created.container_id)
            identity = _require_owner(
                metadata,
                owner=created.owner,
                identity=created.container_id,
                image=image,
            )
            policy.require_container(metadata, owner=created.owner, job=job.path)
            assert identity == created.container_id
            assert metadata["State"]["Status"] == "created"
            assert metadata["State"]["Running"] is False
            assert metadata["State"]["StartedAt"] == "0001-01-01T00:00:00Z"
            receipt = {
                "schema_version": "1.0",
                "state": "ACTUAL_REPAIR_CONTAINER_ADMITTED_NOT_STARTED",
                "container_id": created.container_id,
                "owner": created.owner,
                "image_id": created.image_id,
                "snapshot_sha256": created.snapshot_sha256,
                "suite_sha256": job.suite_sha256,
                "container_status": metadata["State"]["Status"],
                "running": metadata["State"]["Running"],
                "execution_verified": False,
                "promotion_allowed": False,
            }

    assert not job.path.exists()
    assert job.pending_containers == ()
    removed = local_docker_command(
        shutil.which("docker"), "container", "inspect", created.container_id
    )
    assert removed.returncode != 0
    receipt["removed"] = True
    report.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_repair_execution_is_bounded_at_most_once_and_unattested(tmp_path, repository, monkeypatch):
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs"
    with prepared_repair_job(evidence, PROGRAM, suite_sha256=SUITE_SHA, root=root) as job:
        fixture = DockerFixture(monkeypatch, job)
        policy = DockerConfinement(IMAGE, SUITE_SHA)
        starts = []

        def attached(executable, *arguments, timeout, output_limit=65536):
            starts.append((executable, arguments, timeout, output_limit))
            fixture.started = True
            return CommandResult(0, b"verifier-result\n", b"")

        monkeypatch.setattr(module(), "local_docker_command", attached)
        with module().prepared_repair_container(policy, job) as created:
            receipt = module().run_repair_verifier(policy, job, created)
            assert receipt.schema_version == "1.0"
            assert receipt.state == "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
            assert receipt.exit_code == 0
            assert receipt.oom_killed is False
            assert receipt.stdout_bytes == len(b"verifier-result\n")
            assert receipt.execution_verified is False
            assert receipt.promotion_allowed is False
            assert receipt.oom_killed is False
            assert starts == [
                (
                    "owned-docker-fixture",
                    ("container", "start", "--attach", CID),
                    45,
                    65536,
                )
            ]
            intent = root / f"repair-execution-{created.owner}.start-intent.json"
            result = root / f"repair-execution-{created.owner}.result.json"
            stdout = root / f"repair-execution-{created.owner}.stdout"
            assert json.loads(intent.read_bytes())["state"] == "START_REQUEST_MAY_HAVE_BEEN_SENT"
            serialized = json.loads(result.read_bytes())
            assert serialized["snapshot_sha256"] == evidence.snapshot_sha256
            from jsonschema import validate

            validate(
                serialized,
                json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "docs/schemas/repair-execution-receipt.schema.json"
                    ).read_bytes()
                ),
            )
            assert stdout.read_bytes() == b"verifier-result\n"
            with pytest.raises(ValueError, match="intent"):
                module().run_repair_verifier(policy, job, created)
            assert len(starts) == 1


def test_ambiguous_repair_start_keeps_intent_and_never_retries(tmp_path, repository, monkeypatch):
    root = tmp_path / "jobs"
    starts = []
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=root
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        policy = DockerConfinement(IMAGE, SUITE_SHA)

        def ambiguous(executable, *arguments, timeout, output_limit=65536):
            starts.append(arguments)
            raise TimeoutError("attached worker outcome is ambiguous")

        monkeypatch.setattr(module(), "local_docker_command", ambiguous)
        with module().prepared_repair_container(policy, job) as created:
            with pytest.raises(TimeoutError, match="ambiguous"):
                module().run_repair_verifier(policy, job, created)
            intent = root / f"repair-execution-{created.owner}.start-intent.json"
            assert intent.is_file()
            assert not (root / f"repair-execution-{created.owner}.result.json").exists()
            with pytest.raises(ValueError, match="intent"):
                module().run_repair_verifier(policy, job, created)
        assert len(starts) == 1
        assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", CID)


def test_nonzero_verifier_exit_is_retained_as_failure_without_authority(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        fixture.terminal_exit_code = 3
        policy = DockerConfinement(IMAGE, SUITE_SHA)

        def failed(executable, *arguments, timeout):
            fixture.started = True
            return CommandResult(3, b"", b"trusted suite failed")

        monkeypatch.setattr(module(), "local_docker_command", failed)
        with module().prepared_repair_container(policy, job) as created:
            receipt = module().run_repair_verifier(policy, job, created)
            assert receipt.state == "EXECUTION_FAILED_AWAITING_REVIEW"
            assert receipt.exit_code == 3
            assert receipt.stderr_bytes == len(b"trusted suite failed")
            assert receipt.execution_verified is False
            assert receipt.promotion_allowed is False
            assert receipt.oom_killed is False


def test_execution_status_reader_verifies_receipt_after_job_cleanup(
    tmp_path, repository, monkeypatch
):
    root = tmp_path / "jobs"
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=root
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        policy = DockerConfinement(IMAGE, SUITE_SHA)

        def completed(executable, *arguments, timeout):
            fixture.started = True
            return CommandResult(0, b"verified bytes", b"")

        monkeypatch.setattr(module(), "local_docker_command", completed)
        with module().prepared_repair_container(policy, job) as created:
            original = module().run_repair_verifier(policy, job, created)
            owner = created.owner

    monkeypatch.setattr(
        module(),
        "local_docker_command",
        lambda *args, **kwargs: pytest.fail("status reader must not contact Docker"),
    )
    report = module().read_repair_execution(root, owner)
    assert report.state == "EXECUTION_RECORDED"
    assert report.receipt == original
    assert report.retained_artifacts == (
        "result.json",
        "start-intent.json",
        "stderr",
        "stdout",
        "request.json",
    )
    (root / f"repair-execution-{owner}.stdout").write_bytes(b"altered bytes")
    with pytest.raises(ValueError, match="does not match retained evidence"):
        module().read_repair_execution(root, owner)


def test_execution_status_reader_marks_intent_without_result_uncertain(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    owner = "a" * 32
    intent = {
        "schema_version": "1.0",
        "owner": owner,
        "container_id": "b" * 64,
        "image_id": IMAGE,
        "snapshot_sha256": "c" * 64,
        "suite_sha256": SUITE_SHA,
        "request_sha256": "d" * 64,
        "arguments": ["container", "start", "--attach", "b" * 64],
        "timeout_seconds": 45,
        "output_limit": 65536,
        "state": "START_REQUEST_MAY_HAVE_BEEN_SENT",
        "execution_verified": False,
        "promotion_allowed": False,
    }
    (root / f"repair-execution-{owner}.start-intent.json").write_bytes(
        module().canonical_json(intent).encode()
    )

    report = module().read_repair_execution(root, owner)

    assert report.state == "EXECUTION_OUTCOME_UNCONFIRMED"
    assert report.receipt is None
    assert report.retained_artifacts == ("start-intent.json",)


def test_oom_killed_verifier_is_retained_as_failure_even_when_exit_is_zero(
    tmp_path, repository, monkeypatch
):
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=tmp_path / "jobs"
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        fixture.oom_killed = True
        policy = DockerConfinement(IMAGE, SUITE_SHA)

        def completed(executable, *arguments, timeout):
            fixture.started = True
            return CommandResult(0, b"", b"")

        monkeypatch.setattr(module(), "local_docker_command", completed)
        with module().prepared_repair_container(policy, job) as created:
            receipt = module().run_repair_verifier(policy, job, created)
            assert receipt.state == "EXECUTION_FAILED_AWAITING_REVIEW"
            assert receipt.exit_code == 0
            assert receipt.oom_killed is True
            assert receipt.execution_verified is False
            assert receipt.promotion_allowed is False


def test_attached_exit_mismatch_preserves_intent_without_success_receipt(
    tmp_path, repository, monkeypatch
):
    root = tmp_path / "jobs"
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=root
    ) as job:
        fixture = DockerFixture(monkeypatch, job)
        fixture.terminal_exit_code = 1
        policy = DockerConfinement(IMAGE, SUITE_SHA)

        def mismatched(executable, *arguments, timeout):
            fixture.started = True
            return CommandResult(0, b"uncorroborated", b"")

        monkeypatch.setattr(module(), "local_docker_command", mismatched)
        with module().prepared_repair_container(policy, job) as created:
            with pytest.raises(ValueError, match="differs"):
                module().run_repair_verifier(policy, job, created)
            assert (root / f"repair-execution-{created.owner}.start-intent.json").is_file()
            assert (root / f"repair-execution-{created.owner}.stdout").read_bytes() == (
                b"uncorroborated"
            )
            assert not (root / f"repair-execution-{created.owner}.result.json").exists()


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_REPAIR_EXECUTION_ENGINE_TEST") != "1",
    reason="Explicit real-engine isolated repair execution gate required",
)
def test_actual_repair_verifier_runs_once_in_pinned_container_and_retains_unattested_result(
    tmp_path, repository
):
    from ai_quant_os.repair_containers import run_repair_verifier
    from ai_quant_os.repair_sandbox import local_docker_command

    image = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"
    program = b"print('trusted-repair-execution-probe')\n"
    suite_sha = sha256(program).hexdigest()
    policy = DockerConfinement(image, suite_sha)
    assert policy.probe().available
    evidence = snapshot(tmp_path, repository)
    root = tmp_path / "jobs"

    with prepared_repair_job(evidence, program, suite_sha256=suite_sha, root=root) as job:
        with module().prepared_repair_container(policy, job) as created:
            receipt = run_repair_verifier(policy, job, created)
            assert receipt.state == "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
            assert receipt.exit_code == 0
            assert receipt.oom_killed is False
            assert receipt.execution_verified is False
            assert receipt.promotion_allowed is False
            assert receipt.stderr_bytes == 0
            stdout = root / f"repair-execution-{created.owner}.stdout"
            assert stdout.read_bytes() == b"trusted-repair-execution-probe\n"
            report = root / f"repair-execution-{created.owner}.result.json"
            assert json.loads(report.read_bytes())["suite_sha256"] == suite_sha
            container_id = created.container_id

    assert not job.path.exists()
    assert job.pending_containers == ()
    inspected = local_docker_command(shutil.which("docker"), "container", "inspect", container_id)
    assert inspected.returncode != 0
