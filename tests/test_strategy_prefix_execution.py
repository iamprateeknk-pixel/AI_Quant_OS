"""Simulated owned worker start/output/deadline/cleanup; no candidate execution."""

from copy import deepcopy
from hashlib import sha256
import importlib
import json

import pytest

from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.repair_transport import CommandResult
from ai_quant_os.strategy_container_journal import StrategyContainerJournal
from test_strategy_container_lifecycle import (
    job as job,
    runtime as runtime,
    package as package,
    assembled as assembled,
    inputs as inputs,
    daemon as daemon,
)


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_execution"), (
        "Owned prefix execution controller is required"
    )
    return importlib.import_module("ai_quant_os.strategy_prefix_execution")


@pytest.fixture
def execution(runtime, job, package, monkeypatch):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    e = module()
    original = c.local_docker_command
    target = CandidateTargetOutput(invocation_ref=job.invocation_ref, target=1)
    state["start"] = CommandResult(0, target.model_dump_json().encode(), b"")
    state["exit"] = {
        "Status": "exited",
        "Running": False,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "Pid": 0,
        "ExitCode": 0,
        "Error": "",
        "StartedAt": "2026-10-05T00:00:00Z",
        "FinishedAt": "2026-10-05T00:00:01Z",
    }
    state["start_error"] = None
    state["kill_error"] = None
    state["tamper_request"] = False
    state["timeouts"] = []

    def command(executable, *argv, **options):
        if argv[:2] == ("container", "start"):
            calls.append(argv)
            state["timeouts"].append(options.get("timeout"))
            markers = list(job.path.parent.glob("*.start-attempt.json"))
            assert len(markers) == 1
            value = json.loads(markers[0].read_bytes())
            assert (
                value["container_id"] == "3" * 64 and value["create_sha256"] == plan.create_sha256
            )
            assert value["arguments"] == ["container", "start", "--attach", "3" * 64]
            if state["start_error"] is not None:
                state["inspect"]["State"].update(Status="running", Running=True)
                raise state["start_error"]
            state["inspect"]["State"] = deepcopy(state["exit"])
            if state["tamper_request"]:
                request = job.path / "request.json"
                request.chmod(0o644)
                request.write_bytes(b"{}")
            return state["start"]
        if argv[:2] == ("container", "kill"):
            calls.append(argv)
            if state["kill_error"] is not None:
                raise state["kill_error"]
            state["inspect"]["State"] = deepcopy(state["exit"])
            state["inspect"]["State"]["ExitCode"] = 137
            return CommandResult(0, b"3" * 64 + b"\n", b"")
        return original(executable, *argv, **options)

    monkeypatch.setattr(c, "local_docker_command", command)
    return e, c, plan, args, calls, state, target


def test_one_attached_start_is_reserved_before_rpc_and_target_is_saved_only_after_removal(
    execution, job, package
):
    e, c, plan, args, calls, state, target = execution
    receipt = e.execute_strategy_prefix(plan, job, package, **args)
    assert receipt.target == target and receipt.removal.state == "REMOVED"
    assert receipt.execution_verified is False and receipt.promotion_allowed is False
    assert receipt.target.isolation_verified is False
    assert state["timeouts"] == [45]
    assert len([a for a in calls if a[:2] == ("container", "start")]) == 1
    assert (
        job.path.parent / (job.path.name + ".target.json")
    ).read_bytes() == target.model_dump_json().encode()
    assert (job.path / "request.json").exists()
    with pytest.raises((ValueError, FileExistsError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert len([a for a in calls if a[:2] == ("container", "start")]) == 1
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "REMOVED"


@pytest.mark.parametrize(
    "failure", [TimeoutError("deadline"), ValueError("output overflow"), OSError("transport")]
)
def test_failed_or_uncertain_start_kills_and_removes_exact_owned_container_without_output(
    execution, job, package, failure
):
    e, c, plan, args, calls, state, target = execution
    state["start_error"] = failure
    with pytest.raises((ValueError, RuntimeError, OSError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert ("container", "kill", "--signal", "SIGKILL", "3" * 64) in calls
    assert ("container", "rm", "--force", "--volumes", "3" * 64) in calls
    assert not (job.path.parent / (job.path.name + ".target.json")).exists()
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "REMOVED"
    assert len([a for a in calls if a[:2] == ("container", "start")]) == 1


@pytest.mark.parametrize(
    "change",
    ["nonzero", "stderr", "extra", "oversized", "foreign", "boolean", "authority", "noncanonical"],
)
def test_bad_attached_output_never_becomes_a_receipt_and_owned_cleanup_still_runs(
    execution, job, package, change
):
    e, c, plan, args, calls, state, target = execution
    raw = target.model_dump_json().encode()
    if change == "nonzero":
        state["start"] = CommandResult(2, raw, b"")
    elif change == "stderr":
        state["start"] = CommandResult(0, raw, b"warning")
    elif change == "extra":
        state["start"] = CommandResult(0, raw + raw, b"")
    elif change == "oversized":
        state["start"] = CommandResult(0, b"x" * 2049, b"")
    elif change == "foreign":
        state["start"] = CommandResult(
            0,
            CandidateTargetOutput(invocation_ref="candidate-invocation:" + "0" * 64, target=1)
            .model_dump_json()
            .encode(),
            b"",
        )
    else:
        value = json.loads(raw)
        if change == "boolean":
            value["target"] = True
        elif change == "authority":
            value["isolation_verified"] = True
        state["start"] = CommandResult(0, json.dumps(value).encode(), b"")
    with pytest.raises((ValueError, RuntimeError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert not (job.path.parent / (job.path.name + ".execution.json")).exists()
    assert ("container", "rm", "--force", "--volumes", "3" * 64) in calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("Running", True),
        ("ExitCode", 1),
        ("ExitCode", False),
        ("OOMKilled", True),
        ("Dead", True),
        ("Pid", 1),
        ("Error", "failure"),
        ("StartedAt", "0001-01-01T00:00:00Z"),
        ("FinishedAt", "2026-10-04T00:00:00Z"),
    ],
)
def test_bad_independent_exit_state_never_publishes_target(execution, job, package, key, value):
    e, c, plan, args, calls, state, target = execution
    state["exit"][key] = value
    with pytest.raises((ValueError, RuntimeError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert not (job.path.parent / (job.path.name + ".target.json")).exists()


def test_wrong_removal_ack_retains_uncertain_custody_and_withholds_output(execution, job, package):
    e, c, plan, args, calls, state, target = execution
    state["remove"] = CommandResult(0, b"4" * 64, b"")
    with pytest.raises(RuntimeError):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert not (job.path.parent / (job.path.name + ".execution.json")).exists()
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "REMOVE_INTENT"


def test_changed_input_during_start_still_removes_owned_container_and_refuses_publication(
    execution, job, package
):
    e, c, plan, args, calls, state, target = execution
    state["tamper_request"] = True
    with pytest.raises((ValueError, RuntimeError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert ("container", "rm", "--force", "--volumes", "3" * 64) in calls
    assert not (job.path.parent / (job.path.name + ".execution.json")).exists()


def test_receipt_and_exact_target_restore_offline_only_with_independent_hash(
    execution, job, package, monkeypatch
):
    e, c, plan, args, calls, state, target = execution
    receipt = e.execute_strategy_prefix(plan, job, package, **args)
    path = job.path.parent / (job.path.name + ".execution.json")
    pin = sha256(path.read_bytes()).hexdigest()
    before = len(calls)
    monkeypatch.setattr(c.shutil, "which", lambda name: None)
    restored = e.restore_strategy_prefix_execution(plan, job, package, receipt_sha256=pin)
    assert restored == receipt and len(calls) == before
    with pytest.raises(ValueError):
        e.restore_strategy_prefix_execution(plan, job, package, receipt_sha256="0" * 64)
    output = job.path.parent / (job.path.name + ".target.json")
    output.write_bytes(
        CandidateTargetOutput(invocation_ref=job.invocation_ref, target=-1)
        .model_dump_json()
        .encode()
    )
    with pytest.raises(ValueError):
        e.restore_strategy_prefix_execution(plan, job, package, receipt_sha256=pin)


def test_rehashed_receipt_with_foreign_start_binding_is_refused(execution, job, package):
    e, c, plan, args, calls, state, target = execution
    receipt = e.execute_strategy_prefix(plan, job, package, **args)
    value = receipt.model_dump(mode="json")
    value["start_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        type(receipt).model_validate(value)


def test_execution_schema_matches_model_and_denies_authority(execution, job, package):
    from pathlib import Path
    import jsonschema

    e, c, plan, args, calls, state, target = execution
    receipt = e.execute_strategy_prefix(plan, job, package, **args)
    path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/strategy-prefix-execution.schema.json"
    )
    assert path.is_file(), "Retained execution receipt schema is required"
    schema = json.loads(path.read_bytes())
    assert schema == type(receipt).model_json_schema()
    jsonschema.validate(receipt.model_dump(mode="json"), schema)
    for key in ("execution_verified", "promotion_allowed"):
        value = receipt.model_dump(mode="json")
        value[key] = True
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema)


def test_kill_uncertainty_can_be_resolved_only_by_owned_force_removal(execution, job, package):
    e, c, plan, args, calls, state, target = execution
    state["start_error"] = TimeoutError("deadline")
    state["kill_error"] = OSError("unknown kill")
    with pytest.raises((RuntimeError, OSError)):
        e.execute_strategy_prefix(plan, job, package, **args)
    assert ("container", "rm", "--force", "--volumes", "3" * 64) in calls
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "REMOVED"
    assert not (job.path.parent / (job.path.name + ".execution.json")).exists()


def test_closed_custody_session_cannot_inspect_or_mutate_daemon(execution, job, package):
    e, c, plan, args, calls, state, target = execution
    with c.strategy_container_custody(plan, job, package) as custody:
        assert custody.current().state == "CREATE_ACK"
    count = len(calls)
    for operation in (
        custody.current,
        custody.inspect_owned,
        custody.start_attached,
        custody.remove_owned,
    ):
        with pytest.raises(ValueError):
            operation()
    assert len(calls) == count


def test_operator_execute_is_wired_to_original_archives_and_exact_pins(
    execution, job, package, monkeypatch, capsys
):
    e, c, plan, args, calls, state, target = execution
    cli = importlib.import_module("ai_quant_os.strategy_container_cli")
    argv = [
        "strategy-container",
        "--validation-archive",
        str(job.path.parent.parent / "service.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--index",
        "2",
        "--root",
        str(job.path.parent),
        "--job-name",
        job.path.name,
        "--stage-sha256",
        job.stage_sha256,
        "execute",
        "--create-sha256",
        plan.create_sha256,
        "--ownership-sha256",
        plan.ownership_sha256,
    ]
    for key, value in args.items():
        argv.extend(
            ["--image-archive" if key == "archive" else "--" + key.replace("_", "-"), str(value)]
        )
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    value = json.loads(capsys.readouterr().out)
    assert (
        value["state"] == "UNATTESTED_PREFIX_COMPLETED_AND_REMOVED"
        and value["target"]["target"] == 1
    )
    assert value["execution_verified"] is False and value["promotion_allowed"] is False
