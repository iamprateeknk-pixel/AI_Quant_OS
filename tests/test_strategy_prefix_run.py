"""Full prefix orchestration against scripted daemon observations; execute no proposed source."""

from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.repair_transport import CommandResult
from test_strategy_prefix_worker import package as package
from test_strategy_image_loading import (
    assembled as assembled,
    inputs as inputs,
    daemon as daemon,
    arguments,
)
from test_repair_sandbox import container


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_run"), (
        "Complete durable prefix-run controller is required"
    )
    return importlib.import_module("ai_quant_os.strategy_prefix_run")


@pytest.fixture
def scripted(daemon, assembled, tmp_path, monkeypatch):
    import ai_quant_os.strategy_container_lifecycle as c
    from ai_quant_os.strategy_container_contracts import StrategyContainerOwnership
    from ai_quant_os.strategy_container_policy import strategy_labels, CMD

    root = tmp_path / "run"
    root.mkdir()
    _, engine, version, image, _ = daemon
    args = arguments(assembled)
    calls = []
    records = {}
    sequence = 0
    monkeypatch.setattr(c.shutil, "which", lambda name: "docker")

    def command(executable, *argv, **kwargs):
        nonlocal sequence
        calls.append(argv)
        if argv[0] == "info":
            return CommandResult(0, json.dumps(engine).encode(), b"")
        if argv[0] == "version":
            return CommandResult(0, json.dumps(version).encode(), b"")
        if argv[:2] == ("container", "create"):
            sequence += 1
            cid = f"{sequence + 100:064x}"
            source = argv[argv.index("--mount") + 1].split("source=", 1)[1].split(",target=", 1)[0]
            job = Path(source)
            ownership = StrategyContainerOwnership.model_validate_json(
                (root / (job.name + ".container.json")).read_bytes()
            )
            evidence = container(job)
            evidence["Id"] = cid
            evidence["Name"] = "/" + ownership.container_name
            evidence["Image"] = ownership.image_id
            evidence["Config"].update(
                Image=ownership.image_id,
                Labels=strategy_labels(ownership),
                Cmd=list(CMD),
                Env=image["Config"]["Env"],
                Healthcheck={"Test": ["NONE"]},
            )
            evidence["HostConfig"].update(
                RestartPolicy={"Name": "no", "MaximumRetryCount": 0},
                AutoRemove=False,
                Init=False,
                Mounts=[{"Type": "bind", "Source": str(job), "Target": "/job", "ReadOnly": True}],
            )
            records[cid] = (evidence, job)
            return CommandResult(0, cid.encode() + b"\n", b"")
        if argv[:2] == ("container", "inspect"):
            item = records.get(argv[2])
            if item is None:
                item = next((r for r in records.values() if r[0]["Name"] == "/" + argv[2]), None)
            return (
                CommandResult(0, json.dumps(item[0]).encode(), b"")
                if item
                else CommandResult(1, b"", b"unknown")
            )
        if argv[:2] == ("container", "start"):
            evidence, job = records[argv[-1]]
            value = json.loads((job / "request.json").read_bytes())
            evidence["State"] = {
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
            target = CandidateTargetOutput(
                invocation_ref="candidate-invocation:"
                + sha256((job / "request.json").read_bytes()).hexdigest(),
                target=1 if value["index"] == 2 else -1 if value["index"] == 3 else 0,
            )
            return CommandResult(0, target.model_dump_json().encode(), b"")
        if argv[:2] == ("container", "rm"):
            cid = argv[-1]
            records.pop(cid)
            return CommandResult(0, cid.encode() + b"\n", b"")
        raise AssertionError(argv)

    monkeypatch.setattr(c, "local_docker_command", command)
    return root, args, calls, records


def prepare(package, scripted):
    root, args, calls, records = scripted
    return module().prepare_strategy_prefix_run(package, root=root, **args)


def run(package, scripted, manifest):
    return module().run_strategy_prefixes(package, root=scripted[0], run_sha256=manifest.run_sha256)


def test_full_original_coverage_runs_once_releases_inputs_and_replays_native(package, scripted):
    manifest = prepare(package, scripted)
    report = run(package, scripted, manifest)
    root, args, calls, records = scripted
    assert len(report.replay.collection.receipts) == package.holdout.receipt.row_count - 2
    assert report.replay.replay.engine_version == "1.221.0"
    assert report.replay.replay.fills[1].quantity == 2
    assert report.execution_verified is False and report.promotion_allowed is False
    assert not records and not [p for p in root.glob("strategy-job-*") if p.is_dir()]
    starts = [c for c in calls if c[:2] == ("container", "start")]
    assert len(starts) == package.holdout.receipt.row_count - 2
    count = len(calls)
    assert run(package, scripted, manifest) == report
    assert len(calls) == count


@pytest.mark.parametrize("phase", ["stage", "prepare", "create", "execute", "archive", "release"])
def test_uncertain_phase_cannot_automatically_repeat_source_or_allocate_another_job(
    package, scripted, monkeypatch, phase
):
    manifest = prepare(package, scripted)
    names = {
        "stage": "stage_prefix_job",
        "prepare": "prepare_strategy_container",
        "create": "create_strategy_container",
        "execute": "execute_strategy_prefix",
        "archive": "prepare_strategy_prefix_release",
        "release": "release_strategy_prefix_inputs",
    }
    original = getattr(module(), names[phase])

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated outcome unknown")

    monkeypatch.setattr(module(), names[phase], uncertain)
    with pytest.raises(RuntimeError):
        run(package, scripted, manifest)
    before = len(scripted[2])
    jobs = list(scripted[0].glob("strategy-job-*"))
    monkeypatch.setattr(module(), names[phase], original)
    if phase == "release":
        report = run(package, scripted, manifest)
        assert report.execution_verified is False
        assert (
            len([call for call in scripted[2] if call[:2] == ("container", "start")])
            == package.holdout.receipt.row_count - 2
        )
        starts = [call for call in scripted[2] if call[:2] == ("container", "start")]
        assert len(set(starts)) == len(starts)
        assert not [path for path in scripted[0].glob("strategy-job-*") if path.is_dir()]
        return
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert len(scripted[2]) == before and list(scripted[0].glob("strategy-job-*")) == jobs


def test_pinned_completed_release_recovers_only_original_reserved_job_without_source_retry(
    package, scripted, monkeypatch
):
    manifest = prepare(package, scripted)
    original = module().release_strategy_prefix_inputs

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after exact release")

    monkeypatch.setattr(module(), "release_strategy_prefix_inputs", uncertain)
    with pytest.raises(RuntimeError):
        run(package, scripted, manifest)
    monkeypatch.setattr(module(), "release_strategy_prefix_inputs", original)
    archive = next(scripted[0].glob("*.release.json"))
    before = len([c for c in scripted[2] if c[:2] == ("container", "start")])
    module().recover_strategy_prefix_run(
        package,
        root=scripted[0],
        run_sha256=manifest.run_sha256,
        release_sha256=sha256(archive.read_bytes()).hexdigest(),
    )
    assert len([c for c in scripted[2] if c[:2] == ("container", "start")]) == before
    report = run(package, scripted, manifest)
    assert len(report.replay.collection.receipts) == package.holdout.receipt.row_count - 2
    assert (
        len([c for c in scripted[2] if c[:2] == ("container", "start")])
        == package.holdout.receipt.row_count - 2
    )


def test_independent_manifest_pin_and_corrupt_journal_refuse_before_worker_action(
    package, scripted
):
    import sqlite3

    manifest = prepare(package, scripted)
    before = len(scripted[2])
    with pytest.raises(ValueError):
        module().run_strategy_prefixes(package, root=scripted[0], run_sha256="f" * 64)
    assert len(scripted[2]) == before
    with sqlite3.connect(scripted[0] / "strategy-run.sqlite3") as db:
        db.execute("DELETE FROM audit_head")
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert len(scripted[2]) == before


def test_named_staging_obeys_reserved_identity_and_refuses_unsafe_names(package, tmp_path):
    from ai_quant_os.strategy_prefix_jobs import stage_prefix_job

    root = tmp_path / "jobs"
    root.mkdir()
    name = "strategy-job-" + "1" * 32
    job = stage_prefix_job(package, 2, root=root, job_name=name)
    assert job.path.name == name
    with pytest.raises((ValueError, OSError)):
        stage_prefix_job(package, 2, root=root, job_name=name)
    with pytest.raises(ValueError):
        stage_prefix_job(package, 2, root=root, job_name="../escape")
    assert not (tmp_path / "escape").exists()


def test_full_run_keeps_base_and_declared_stress_replay_bound_to_one_source_inventory(
    package, scripted
):
    manifest = prepare(package, scripted)
    report = run(package, scripted, manifest)
    assert report.stress_replay.collection == report.replay.collection
    assert report.stress_replay.replay.cost_scenario == "DECLARED_STRESS"
    assert report.replay.replay.cost_scenario == "BASE"


def test_report_publication_unknown_requires_independent_recovery_without_rerunning_workers(
    package, scripted, monkeypatch
):
    manifest = prepare(package, scripted)
    original = module()._publish

    def uncertain(path, raw):
        original(path, raw)
        if path.name == "strategy-run-replay.json":
            raise OSError("after report publication")

    monkeypatch.setattr(module(), "_publish", uncertain)
    with pytest.raises(OSError):
        run(package, scripted, manifest)
    monkeypatch.setattr(module(), "_publish", original)
    before = len(scripted[2])
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert len(scripted[2]) == before
    raw = (scripted[0] / "strategy-run-replay.json").read_bytes()
    report = module().recover_strategy_prefix_run_report(
        package,
        root=scripted[0],
        run_sha256=manifest.run_sha256,
        report_sha256=sha256(raw).hexdigest(),
    )
    assert report == run(package, scripted, manifest) and len(scripted[2]) == before


def test_untrusted_worker_output_halts_the_run_after_owned_cleanup_and_cannot_repeat(
    package, scripted, monkeypatch
):
    import ai_quant_os.strategy_container_lifecycle as c

    manifest = prepare(package, scripted)
    original = c.local_docker_command

    def bad_output(executable, *argv, **kwargs):
        result = original(executable, *argv, **kwargs)
        if argv[:2] == ("container", "start"):
            return CommandResult(0, b'{"target":true}', b"")
        return result

    monkeypatch.setattr(c, "local_docker_command", bad_output)
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert not scripted[3]
    before = len(scripted[2])
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert len(scripted[2]) == before
    assert len([c for c in scripted[2] if c[:2] == ("container", "start")]) == 1


def test_actual_operator_prepare_run_and_retained_schemas(
    package, scripted, tmp_path, monkeypatch, capsys
):
    import sys
    import jsonschema
    from ai_quant_os.validation_package_store import ValidationPackageStore

    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_run_cli"), (
        "Full run operator commands are required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_prefix_run_cli")
    archive = tmp_path / "packages.sqlite3"
    with ValidationPackageStore(archive) as store:
        store.put(package)
    common = [
        "prefix-run",
        "--validation-archive",
        str(archive),
        "--package-ref",
        package.package_ref,
        "--root",
        str(scripted[0]),
    ]
    image = []
    for key, value in scripted[1].items():
        image.extend(
            ["--" + ("image-archive" if key == "archive" else key.replace("_", "-")), str(value)]
        )
    monkeypatch.setattr(sys, "argv", common + ["prepare", *image])
    assert cli.main() == 0
    pin = json.loads(capsys.readouterr().out)["run_sha256"]
    monkeypatch.setattr(sys, "argv", common + ["run", "--run-sha256", pin])
    assert cli.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "UNATTESTED_FULL_PREFIX_RUN_NATIVE_REPLAY"
    report = module().StrategyPrefixRunReport.model_validate_json(
        (scripted[0] / "strategy-run-replay.json").read_bytes()
    )
    assert report.report_ref == summary["report_ref"]
    for value, name in (
        (report.manifest, "strategy-prefix-run-manifest"),
        (report, "strategy-prefix-run-report"),
    ):
        path = Path(__file__).resolve().parents[1] / ("docs/schemas/" + name + ".schema.json")
        assert path.exists(), "Full run wire schemas must be retained"
        schema = json.loads(path.read_bytes())
        assert schema == type(value).model_json_schema()
        jsonschema.validate(value.model_dump(mode="json"), schema)
        forged = value.model_dump(mode="json")
        forged["execution_verified"] = True
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(forged, schema)


def test_quota_and_external_reserved_job_cannot_be_bypassed_by_a_new_run(package, scripted):
    manifest = prepare(package, scripted)
    with pytest.raises(ValueError):
        prepare(package, scripted)
    unknown = scripted[0] / ("strategy-job-" + "f" * 32)
    unknown.mkdir()
    before = len(scripted[2])
    with pytest.raises(ValueError):
        run(package, scripted, manifest)
    assert len(scripted[2]) == before and unknown.exists()


def test_reserved_native_replay_without_any_published_report_can_retry_without_worker_execution(
    package, scripted, monkeypatch
):
    manifest = prepare(package, scripted)
    original = module().replay_strategy_execution_tape

    def fail(*args, **kwargs):
        raise RuntimeError("numeric replay interrupted")

    monkeypatch.setattr(module(), "replay_strategy_execution_tape", fail)
    with pytest.raises(RuntimeError):
        run(package, scripted, manifest)
    assert not (scripted[0] / "strategy-run-replay.json").exists()
    before = len(scripted[2])
    monkeypatch.setattr(module(), "replay_strategy_execution_tape", original)
    report = run(package, scripted, manifest)
    assert report.replay.replay.engine_version == "1.221.0" and len(scripted[2]) == before


def test_rehashed_report_cannot_change_run_quantity_and_initial_balance(package, scripted):
    manifest = prepare(package, scripted)
    report = run(package, scripted, manifest)
    changed = report.model_dump(mode="json")
    changed["replay"] = (
        module()
        .replay_strategy_execution_tape(package, report.replay.collection, quantity=2)
        .model_dump(mode="json")
    )
    changed["stress_replay"] = (
        module()
        .replay_strategy_execution_tape(
            package, report.replay.collection, quantity=2, cost_scenario="DECLARED_STRESS"
        )
        .model_dump(mode="json")
    )
    with pytest.raises(ValueError):
        type(report).model_validate(changed)


def test_completed_receipt_is_committed_before_release_and_resumes_without_source_retry(
    package, scripted, monkeypatch
):
    manifest = prepare(package, scripted)
    original = module().release_strategy_prefix_inputs
    failed = False

    def uncertain(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("before exact release")
        return original(*args, **kwargs)

    monkeypatch.setattr(module(), "release_strategy_prefix_inputs", uncertain)
    with pytest.raises(OSError):
        run(package, scripted, manifest)
    from ai_quant_os.strategy_prefix_run_store import StrategyPrefixRunStore

    with StrategyPrefixRunStore(scripted[0], manifest) as store:
        rows, final = store.snapshot()
        assert rows[2].phase == "COMPLETED" and final is None
    starts = [c for c in scripted[2] if c[:2] == ("container", "start")]
    assert len(starts) == 1 and (scripted[0] / (rows[2].job_name + ".release.json")).exists()
    first_start = starts[0]
    monkeypatch.setattr(module(), "release_strategy_prefix_inputs", original)
    report = run(package, scripted, manifest)
    assert report.replay.replay.engine_version == "1.221.0"
    starts = [call for call in scripted[2] if call[:2] == ("container", "start")]
    assert len(starts) == package.holdout.receipt.row_count - 2
    assert starts.count(first_start) == 1 and len(set(starts)) == len(starts)
