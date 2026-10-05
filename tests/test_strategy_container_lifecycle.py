"""Strategy create/admission/removal against a simulated daemon; execute no source."""

from copy import deepcopy
import importlib
import json

import pytest

from ai_quant_os.repair_transport import CommandResult
from ai_quant_os.strategy_container_journal import StrategyContainerJournal
from ai_quant_os.strategy_container_policy import (
    CMD,
    require_created_strategy_container,
    strategy_labels,
)
from ai_quant_os.strategy_prefix_jobs import stage_prefix_job
from test_strategy_prefix_worker import package as package
from test_strategy_image_loading import (
    assembled as assembled,
    inputs as inputs,
    daemon as daemon,
    arguments,
)
from test_repair_sandbox import container


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_container_lifecycle"), (
        "Strategy container controller is required"
    )
    return importlib.import_module("ai_quant_os.strategy_container_lifecycle")


@pytest.fixture
def job(package, tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    return stage_prefix_job(package, 2, root=root)


@pytest.fixture
def runtime(daemon, assembled, job, package, monkeypatch):
    controller = module()
    calls, engine, version, image, failures = daemon
    args = arguments(assembled)
    controller_calls = []
    state = {
        "inspect": None,
        "create": CommandResult(0, b"3" * 64 + b"\n", b""),
        "remove": CommandResult(0, b"3" * 64 + b"\n", b""),
    }
    monkeypatch.setattr(controller.shutil, "which", lambda name: "docker")

    def command(executable, *argv, **kwargs):
        controller_calls.append(argv)
        if argv[0] == "info":
            return CommandResult(0, json.dumps(engine).encode(), b"")
        if argv[0] == "version":
            return CommandResult(0, json.dumps(version).encode(), b"")
        if argv[:2] == ("container", "create"):
            with StrategyContainerJournal(job.path.parent) as journal:
                assert journal.event_count == 1
            assert list(job.path.parent.glob("*.create-attempt.json"))
            return state["create"]
        if argv[:2] == ("container", "inspect"):
            if state["inspect"] is None:
                return CommandResult(1, b"", b"unknown")
            return CommandResult(0, json.dumps(state["inspect"]).encode(), b"")
        if argv[:2] == ("container", "rm"):
            return state["remove"]
        raise AssertionError(argv)

    monkeypatch.setattr(controller, "local_docker_command", command)
    plan = controller.prepare_strategy_container(job, package, **args)
    evidence = container(job.path)
    evidence["Id"] = "3" * 64
    evidence["Name"] = "/" + plan.ownership.container_name
    evidence["Image"] = plan.ownership.image_id
    evidence["Config"].update(
        {
            "Image": plan.ownership.image_id,
            "Labels": strategy_labels(plan.ownership),
            "Cmd": list(CMD),
            "Env": image["Config"]["Env"],
            "Healthcheck": {"Test": ["NONE"]},
        }
    )
    evidence["HostConfig"].update(
        {
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "Init": False,
            "Mounts": [
                {"Type": "bind", "Source": str(job.path), "Target": "/job", "ReadOnly": True}
            ],
        }
    )
    state["inspect"] = evidence
    return controller, plan, args, controller_calls, state, engine


def test_exact_request_is_persisted_then_created_and_admitted_once(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    assert c.create_strategy_container(plan, job, package, **args) == "3" * 64
    create = [a for a in calls if a[:2] == ("container", "create")]
    assert len(create) == 1 and "--pull" in create[0] and "never" in create[0]
    assert create[0][-len(CMD) :] == CMD
    assert not any(a[:2] in [("container", "start"), ("container", "run")] for a in calls)
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "CREATE_ACK"
    with pytest.raises((ValueError, FileExistsError)):
        c.create_strategy_container(plan, job, package, **args)
    assert len([a for a in calls if a[:2] == ("container", "create")]) == 1


def test_docker_created_output_capture_is_admitted_without_interactive_input(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["Config"].update(AttachStdout=True, AttachStderr=True)
    assert c.create_strategy_container(plan, job, package, **args) == "3" * 64


@pytest.mark.parametrize("field", ["AttachStdout", "AttachStderr"])
@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_output_capture_requires_actual_boolean_metadata(runtime, job, package, field, value):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["Config"][field] = value
    with pytest.raises(RuntimeError, match="uncertain"):
        c.create_strategy_container(plan, job, package, **args)


@pytest.mark.parametrize("field", ["Tty", "OpenStdin", "StdinOnce", "AttachStdin"])
def test_output_capture_does_not_admit_interactive_input(runtime, job, package, field):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["Config"].update(AttachStdout=True, AttachStderr=True)
    state["inspect"]["Config"][field] = True
    with pytest.raises(RuntimeError, match="uncertain"):
        c.create_strategy_container(plan, job, package, **args)


MASKED_PATHS = [
    "/proc/acpi",
    "/proc/asound",
    "/proc/interrupts",
    "/proc/kcore",
    "/proc/keys",
    "/proc/latency_stats",
    "/proc/sched_debug",
    "/proc/scsi",
    "/proc/timer_list",
    "/proc/timer_stats",
    "/sys/devices/virtual/powercap",
    "/sys/firmware",
]


def test_docker_default_masks_are_order_independent_and_cgroup_selector_is_empty(
    runtime, job, package
):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["HostConfig"].update(Cgroup="", MaskedPaths=MASKED_PATHS)
    assert c.create_strategy_container(plan, job, package, **args) == "3" * 64


@pytest.mark.parametrize(
    "paths", [MASKED_PATHS[:-1], MASKED_PATHS + ["/extra"], MASKED_PATHS + [MASKED_PATHS[0]]]
)
def test_changed_mask_inventory_is_rejected(runtime, job, package, paths):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["HostConfig"]["MaskedPaths"] = paths
    with pytest.raises(RuntimeError, match="uncertain"):
        c.create_strategy_container(plan, job, package, **args)


@pytest.mark.parametrize("value", ["host", 0, None])
def test_active_or_untyped_cgroup_selector_is_rejected(runtime, job, package, value):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["HostConfig"]["Cgroup"] = value
    with pytest.raises(RuntimeError, match="uncertain"):
        c.create_strategy_container(plan, job, package, **args)


def test_readonly_kernel_paths_are_order_independent_but_inventory_is_exact(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    paths = ["/proc/sysrq-trigger", "/proc/sys", "/proc/irq", "/proc/fs", "/proc/bus"]
    state["inspect"]["HostConfig"]["ReadonlyPaths"] = paths
    require_created_strategy_container(state["inspect"], plan.ownership, plan.environment)
    for invalid in (paths[:-1], paths + ["/extra"], paths + [paths[0]], [None]):
        state["inspect"]["HostConfig"]["ReadonlyPaths"] = invalid
        with pytest.raises(ValueError, match="inventory"):
            require_created_strategy_container(state["inspect"], plan.ownership, plan.environment)


def test_cleanup_removes_only_independently_inspected_owned_id_and_retains_inputs(
    runtime, job, package
):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    result = c.cleanup_strategy_container(plan, job, package)
    assert result.state == "REMOVED" and result.container_id == "3" * 64
    assert [a for a in calls if a[:2] == ("container", "rm")] == [
        ("container", "rm", "--force", "--volumes", "3" * 64)
    ]
    assert (job.path / "request.json").exists()
    with pytest.raises(ValueError):
        job.discard_unstarted(package)
    count = len(calls)
    assert c.cleanup_strategy_container(plan, job, package) == result
    assert len(calls) == count


@pytest.mark.parametrize(
    "change",
    [
        "privileged",
        "command",
        "environment",
        "mount",
        "bool",
        "extra_mount",
        "restart",
        "owner",
        "id",
        "image",
        "running",
    ],
)
def test_weakened_or_foreign_created_container_is_never_admitted(runtime, job, package, change):
    c, plan, args, calls, state, engine = runtime
    e = state["inspect"]
    if change == "privileged":
        e["HostConfig"]["Privileged"] = True
    elif change == "command":
        e["Config"]["Cmd"] = ["/bin/sh"]
    elif change == "environment":
        e["Config"]["Env"].append("SECRET=value")
    elif change == "mount":
        e["Mounts"][0]["RW"] = True
    elif change == "bool":
        e["HostConfig"]["ReadonlyRootfs"] = 1
    elif change == "extra_mount":
        e["Mounts"].append(deepcopy(e["Mounts"][0]))
    elif change == "restart":
        e["HostConfig"]["RestartPolicy"]["Name"] = "always"
    elif change == "owner":
        e["Config"]["Labels"]["ai-quant.strategy-owner"] = "foreign"
    elif change == "id":
        e["Id"] = "4" * 64
    elif change == "image":
        e["Image"] = "sha256:" + "5" * 64
    elif change == "running":
        e["State"].update(Running=True, Status="running")
    with pytest.raises((ValueError, RuntimeError)):
        c.create_strategy_container(plan, job, package, **args)
    assert not any(a[:2] == ("container", "start") for a in calls)


def test_ambiguous_create_is_not_retried_and_owned_name_can_be_reconciled_for_cleanup(
    runtime, job, package
):
    c, plan, args, calls, state, engine = runtime
    state["create"] = CommandResult(1, b"", b"uncertain")
    with pytest.raises(RuntimeError):
        c.create_strategy_container(plan, job, package, **args)
    with pytest.raises((ValueError, FileExistsError)):
        c.create_strategy_container(plan, job, package, **args)
    assert len([a for a in calls if a[:2] == ("container", "create")]) == 1
    assert c.cleanup_strategy_container(plan, job, package).state == "REMOVED"
    assert any(a[:3] == ("container", "inspect", plan.ownership.container_name) for a in calls)


def test_unknown_absence_never_proves_removal(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    state["create"] = CommandResult(1, b"", b"uncertain")
    with pytest.raises(RuntimeError):
        c.create_strategy_container(plan, job, package, **args)
    state["inspect"] = None
    with pytest.raises((ValueError, RuntimeError)):
        c.cleanup_strategy_container(plan, job, package)
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "CREATE_INTENT"
    assert not any(a[:2] == ("container", "rm") for a in calls)


@pytest.mark.parametrize("sidecar", ["create", "create-attempt", "container"])
def test_changed_or_missing_custody_is_retained_without_daemon_removal(
    runtime, job, package, sidecar
):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    path = job.path.parent / (job.path.name + "." + sidecar + ".json")
    path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        c.cleanup_strategy_container(plan, job, package)
    assert not any(a[:2] == ("container", "rm") for a in calls)
    assert (job.path / "request.json").exists()


def test_wrong_removal_acknowledgement_does_not_release_ownership(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    state["remove"] = CommandResult(0, b"4" * 64, b"")
    with pytest.raises((ValueError, RuntimeError)):
        c.cleanup_strategy_container(plan, job, package)
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "REMOVE_INTENT"
    assert (job.path / "request.json").exists()


def test_changed_engine_prevents_create_without_publishing_attempt(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    engine["ID"] = "different-engine"
    with pytest.raises(ValueError):
        c.create_strategy_container(plan, job, package, **args)
    assert not list(job.path.parent.glob("*.create-attempt.json"))
    assert not any(a[:2] == ("container", "create") for a in calls)


def test_restart_restores_only_independently_pinned_original_recipe(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    restored = c.restore_strategy_container_plan(
        job, package, create_sha256=plan.create_sha256, ownership_sha256=plan.ownership_sha256
    )
    assert restored == plan
    c.create_strategy_container(restored, job, package, **args)
    again = c.restore_strategy_container_plan(
        job, package, create_sha256=plan.create_sha256, ownership_sha256=plan.ownership_sha256
    )
    assert c.cleanup_strategy_container(again, job, package).state == "REMOVED"
    with pytest.raises(ValueError):
        c.restore_strategy_container_plan(
            job, package, create_sha256="0" * 64, ownership_sha256=plan.ownership_sha256
        )


@pytest.mark.parametrize("kind", ["id", "owner", "image"])
def test_cleanup_never_mutates_foreign_container(runtime, job, package, kind):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    if kind == "id":
        state["inspect"]["Id"] = "4" * 64
    elif kind == "owner":
        state["inspect"]["Name"] = "/foreign"
    else:
        state["inspect"]["Image"] = "sha256:" + "4" * 64
    with pytest.raises(ValueError):
        c.cleanup_strategy_container(plan, job, package)
    assert not any(a[:2] == ("container", "rm") for a in calls)


def test_changed_request_prevents_create_before_any_mutating_rpc(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    path = job.path / "request.json"
    path.chmod(0o644)
    path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        c.create_strategy_container(plan, job, package, **args)
    assert not any(a[:2] == ("container", "create") for a in calls)


def test_durable_attempt_publication_failure_never_sends_create(runtime, job, package, monkeypatch):
    c, plan, args, calls, state, engine = runtime

    def failure(*args):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(c.os, "fsync", failure)
    with pytest.raises(OSError):
        c.create_strategy_container(plan, job, package, **args)
    assert not any(a[:2] == ("container", "create") for a in calls)
    assert list(job.path.parent.glob("*.create-attempt.json"))


def test_unknown_create_identity_keeps_intent_and_prevents_retry(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    state["create"] = CommandResult(0, b"not-an-id", b"")
    with pytest.raises(RuntimeError):
        c.create_strategy_container(plan, job, package, **args)
    with StrategyContainerJournal(job.path.parent) as journal:
        assert journal.current(plan.ownership).state == "CREATE_INTENT"
    with pytest.raises(FileExistsError):
        c.create_strategy_container(plan, job, package, **args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("CpuQuota", -1),
        ("Runtime", "untrusted"),
        ("UsernsMode", "host"),
        ("OomKillDisable", True),
        ("PublishAllPorts", True),
        ("UnknownActiveFeature", True),
    ],
)
def test_additional_active_host_features_are_denied(runtime, job, package, field, value):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["HostConfig"][field] = value
    with pytest.raises(RuntimeError):
        c.create_strategy_container(plan, job, package, **args)


def test_operator_cleanup_uses_original_archive_and_independent_plan_pins(
    runtime, job, package, monkeypatch, capsys
):
    c, plan, args, calls, state, engine = runtime
    assert importlib.util.find_spec("ai_quant_os.strategy_container_cli"), (
        "Operator controller CLI is required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_container_cli")
    c.create_strategy_container(plan, job, package, **args)
    argv = [
        "strategy-container",
        "--validation-archive",
        str(job.path.parent.parent / "paper.validation-packages.sqlite3"),
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
        "cleanup",
        "--create-sha256",
        plan.create_sha256,
        "--ownership-sha256",
        plan.ownership_sha256,
    ]
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "REMOVED" and result["promotion_allowed"] is False


def test_operator_cli_missing_archive_does_not_create_it_or_echo_source(
    tmp_path, monkeypatch, capsys
):
    assert importlib.util.find_spec("ai_quant_os.strategy_container_cli"), (
        "Operator controller CLI is required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_container_cli")
    missing = tmp_path / "missing.sqlite3"
    argv = [
        "strategy-container",
        "--validation-archive",
        str(missing),
        "--package-ref",
        "validation-package:" + "0" * 64,
        "--index",
        "2",
        "--root",
        str(tmp_path),
        "--job-name",
        "strategy-job-" + "0" * 32,
        "--stage-sha256",
        "0" * 64,
        "cleanup",
        "--create-sha256",
        "0" * 64,
        "--ownership-sha256",
        "0" * 64,
    ]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and not missing.exists()
    assert "source" not in capsys.readouterr().err.lower()


def test_unknown_created_configuration_is_not_admitted(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    state["inspect"]["Config"]["UnknownActiveFeature"] = True
    with pytest.raises(RuntimeError):
        c.create_strategy_container(plan, job, package, **args)


@pytest.mark.parametrize("bad_job", [None, "not-a-job", {}])
def test_public_controller_rejects_non_jobs_before_side_effects(bad_job):
    c = module()
    with pytest.raises(ValueError):
        c.create_strategy_container(None, bad_job, None, archive=None)
    with pytest.raises(ValueError):
        c.cleanup_strategy_container(None, bad_job, None)
