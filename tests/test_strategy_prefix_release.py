"""Pinned archive before exact removed-prefix deletion; all daemon observations simulated."""

from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from test_strategy_prefix_execution import (
    execution as execution,
    job as job,
    package as package,
    runtime as runtime,
    assembled as assembled,
    inputs as inputs,
    daemon as daemon,
)


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_release"), (
        "Completed prefix custody release is required"
    )
    return importlib.import_module("ai_quant_os.strategy_prefix_release")


@pytest.fixture
def completed(execution, job, package):
    e, c, plan, args, calls, state, target = execution
    receipt = e.execute_strategy_prefix(plan, job, package, **args)
    return plan, receipt, sha256(receipt.model_dump_json().encode()).hexdigest(), calls


def prepare(completed, job, package):
    plan, receipt, pin, calls = completed
    return module().prepare_strategy_prefix_release(plan, job, package, receipt_sha256=pin)


def release(bundle, job, package):
    return module().release_strategy_prefix_inputs(
        package,
        root=job.path.parent,
        job_name=job.path.name,
        release_sha256=sha256(bundle.model_dump_json().encode()).hexdigest(),
    )


def test_archive_is_saved_before_deletion_and_exact_release_is_offline_idempotent(
    completed, job, package, monkeypatch
):
    plan, receipt, pin, calls = completed
    before = len(calls)
    bundle = prepare(completed, job, package)
    assert job.path.is_dir() and (job.path / "request.json").exists()
    archive = job.path.parent / (job.path.name + ".release.json")
    assert archive.read_bytes() == bundle.model_dump_json().encode()
    assert bundle.receipt == receipt and len(bundle.files) == 8
    assert bundle.execution_verified is False and bundle.promotion_allowed is False
    result = release(bundle, job, package)
    assert result.state == "PREFIX_INPUTS_RELEASED"
    assert not job.path.exists()
    assert archive.exists() and (job.path.parent / "strategy-containers.sqlite3").exists()
    assert (job.path.parent / ".strategy-owner.lock").exists()
    assert len(calls) == before
    assert release(bundle, job, package) == result
    assert result.execution_verified is False and result.promotion_allowed is False


@pytest.mark.parametrize("kind", ["receipt_pin", "request", "target", "create_attempt", "journal"])
def test_preparation_refuses_changed_or_unproved_custody_without_deletion(
    completed, job, package, kind
):
    plan, receipt, pin, calls = completed
    if kind == "receipt_pin":
        pin = "f" * 64
    elif kind == "journal":
        from ai_quant_os.strategy_container_journal import StrategyContainerJournal

        with StrategyContainerJournal(job.path.parent) as journal:
            journal._db.execute("DELETE FROM audit_head")
            journal._db.commit()
    else:
        path = (
            job.path / "request.json"
            if kind == "request"
            else job.path.parent
            / (
                job.path.name
                + "."
                + ("create-attempt" if kind == "create_attempt" else kind)
                + ".json"
            )
        )
        path.chmod(0o644)
        path.write_bytes(b"{}")
    with pytest.raises((ValueError, OSError)):
        module().prepare_strategy_prefix_release(plan, job, package, receipt_sha256=pin)
    assert job.path.exists() and (job.path / "request.json").exists()
    assert not (job.path.parent / (job.path.name + ".release.json")).exists()


@pytest.mark.parametrize("where", ["job", "root"])
def test_unknown_files_prevent_release_and_are_never_deleted(completed, job, package, where):
    bundle = prepare(completed, job, package)
    unknown = (
        job.path / "extra.json"
        if where == "job"
        else job.path.parent / (job.path.name + ".unknown.json")
    )
    unknown.write_bytes(b"keep")
    with pytest.raises(ValueError):
        release(bundle, job, package)
    assert unknown.read_bytes() == b"keep" and (job.path / "request.json").exists()


def test_release_keeps_neighbouring_jobs_and_can_free_the_staging_capacity(completed, job, package):
    from ai_quant_os.strategy_prefix_jobs import stage_prefix_job

    for i in range(31):
        (job.path.parent / ("strategy-job-" + f"{i + 100:032x}")).mkdir()
    with pytest.raises(ValueError, match="capacity"):
        stage_prefix_job(package, 2, root=job.path.parent)
    bundle = prepare(completed, job, package)
    release(bundle, job, package)
    new = stage_prefix_job(package, 2, root=job.path.parent)
    assert new.path.is_dir()
    assert len(list(job.path.parent.glob("strategy-job-*"))) > 32
    assert all(
        (job.path.parent / ("strategy-job-" + f"{i + 100:032x}")).is_dir() for i in range(31)
    )


def test_partial_deletion_resumes_only_under_independent_intent_and_attempt_pin(
    completed, job, package, monkeypatch
):
    bundle = prepare(completed, job, package)
    original = Path.unlink

    def fail_stage(path, *args, **kwargs):
        if path == job.stage_path:
            raise OSError("simulated release interruption")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_stage)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert not job.path.exists() and job.stage_path.exists()
    monkeypatch.setattr(Path, "unlink", original)
    result = release(bundle, job, package)
    assert result.state == "PREFIX_INPUTS_RELEASED" and not job.stage_path.exists()


def test_missing_input_without_release_attempt_is_not_assumed_deleted(completed, job, package):
    bundle = prepare(completed, job, package)
    request = job.path / "request.json"
    request.chmod(0o644)
    request.unlink()
    with pytest.raises(ValueError):
        release(bundle, job, package)
    assert job.path.exists() and job.stage_path.exists()


def test_wrong_release_pin_or_rehashed_foreign_request_cannot_authorize_deletion(
    completed, job, package
):
    bundle = prepare(completed, job, package)
    with pytest.raises(ValueError):
        module().release_strategy_prefix_inputs(
            package, root=job.path.parent, job_name=job.path.name, release_sha256="f" * 64
        )
    raw = bundle.model_dump(mode="json")
    import base64

    row = next(row for row in raw["files"] if row["name"] == "request.json")
    row["data_base64"] = base64.b64encode(b"{}").decode()
    row["sha256"] = sha256(b"{}").hexdigest()
    row["identity"][2] = 2
    path = job.path.parent / (job.path.name + ".release.json")
    forged = type(bundle).model_validate(raw)
    path.write_bytes(forged.model_dump_json().encode())
    with pytest.raises(ValueError):
        release(forged, job, package)
    assert job.path.exists() and (job.path / "request.json").exists()


def test_fsync_failure_before_delete_retains_all_inputs(completed, job, package, monkeypatch):
    bundle = prepare(completed, job, package)

    def fail(fd):
        raise OSError("simulated attempt fsync failure")

    monkeypatch.setattr(module().os, "fsync", fail)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert (job.path / "request.json").exists() and job.stage_path.exists()


def test_boolean_link_count_cannot_pass_as_single_link_metadata(completed, job, package):
    bundle = prepare(completed, job, package)
    raw = bundle.model_dump(mode="json")
    raw["files"][0]["identity"][4] = True
    with pytest.raises(ValueError):
        type(bundle).model_validate(raw)


def test_changed_file_identity_after_archive_is_not_adopted(completed, job, package):
    bundle = prepare(completed, job, package)
    path = job.path.parent / (job.path.name + ".target.json")
    before = path.read_bytes()
    path.unlink()
    path.write_bytes(before)
    with pytest.raises(ValueError):
        release(bundle, job, package)
    assert (job.path / "request.json").exists()


def test_attempt_fsync_failure_can_resume_only_after_syncing_the_same_marker(
    completed, job, package, monkeypatch
):
    bundle = prepare(completed, job, package)
    original = module().os.fsync
    count = 0

    def fail_attempt(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("attempt fsync interrupted")
        return original(fd)

    monkeypatch.setattr(module().os, "fsync", fail_attempt)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert (job.path / "request.json").exists()
    assert (job.path.parent / (job.path.name + ".release-attempt.json")).exists()
    monkeypatch.setattr(module().os, "fsync", original)
    assert release(bundle, job, package).state == "PREFIX_INPUTS_RELEASED"


def test_schema_and_operator_release_support_partial_recovery_without_loading_a_missing_job(
    completed, job, package, tmp_path, monkeypatch, capsys
):
    import sys
    import jsonschema
    from ai_quant_os.validation_package_store import ValidationPackageStore

    archive = tmp_path / "packages.sqlite3"
    with ValidationPackageStore(archive) as store:
        store.put(package)
    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_release_cli"), (
        "Explicit prepare/release operator entry point required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_prefix_release_cli")
    plan, receipt, pin, calls = completed
    common = [
        "prefix-release",
        "--validation-archive",
        str(archive),
        "--package-ref",
        package.package_ref,
        "--root",
        str(job.path.parent),
        "--job-name",
        job.path.name,
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        common
        + [
            "prepare",
            "--index",
            str(job.index),
            "--stage-sha256",
            job.stage_sha256,
            "--create-sha256",
            plan.create_sha256,
            "--ownership-sha256",
            plan.ownership_sha256,
            "--receipt-sha256",
            pin,
        ],
    )
    assert cli.main() == 0
    summary = json.loads(capsys.readouterr().out)
    raw = (job.path.parent / (job.path.name + ".release.json")).read_bytes()
    assert summary["release_sha256"] == sha256(raw).hexdigest()
    bundle = module().StrategyPrefixReleaseBundle.model_validate_json(raw)
    monkeypatch.setattr(
        sys, "argv", common + ["release", "--release-sha256", summary["release_sha256"]]
    )
    assert cli.main() == 0 and not job.path.exists()
    result = module().StrategyPrefixReleaseResult.model_validate(
        json.loads(capsys.readouterr().out)
    )
    assert cli.main() == 0
    for value, name in (
        (bundle, "strategy-prefix-release"),
        (result, "strategy-prefix-release-result"),
    ):
        path = Path(__file__).resolve().parents[1] / ("docs/schemas/" + name + ".schema.json")
        assert path.exists(), "Retained recovery/deletion wire schema required"
        schema = json.loads(path.read_bytes())
        assert schema == type(value).model_json_schema()
        jsonschema.validate(value.model_dump(mode="json"), schema)
        forged = value.model_dump(mode="json")
        forged["execution_verified"] = True
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(forged, schema)


@pytest.mark.parametrize("fault", ["attempt", "journal", "root"])
def test_release_requires_same_attempt_audit_and_parent_identities(
    completed, job, package, tmp_path, fault
):
    bundle = prepare(completed, job, package)
    if fault == "attempt":
        (job.path.parent / (job.path.name + ".release-attempt.json")).write_bytes(b"{}")
    elif fault == "journal":
        from ai_quant_os.strategy_container_journal import StrategyContainerJournal

        with StrategyContainerJournal(job.path.parent) as journal:
            journal._db.execute("DELETE FROM audit_head")
            journal._db.commit()
    else:
        root = tmp_path / "other"
        root.mkdir()
        (root / (job.path.name + ".release.json")).write_bytes(bundle.model_dump_json().encode())
        with pytest.raises(ValueError):
            module().release_strategy_prefix_inputs(
                package, root=root, job_name=job.path.name, release_sha256=bundle.release_sha256
            )
        assert (job.path / "request.json").exists()
        return
    with pytest.raises(ValueError):
        release(bundle, job, package)
    assert (job.path / "request.json").exists() and job.stage_path.exists()


def test_unremoved_container_cannot_be_archived_for_release(runtime, job, package):
    c, plan, args, calls, state, engine = runtime
    c.create_strategy_container(plan, job, package, **args)
    with pytest.raises((ValueError, OSError)):
        module().prepare_strategy_prefix_release(plan, job, package, receipt_sha256="0" * 64)
    assert job.path.exists() and not (job.path.parent / (job.path.name + ".release.json")).exists()


def test_request_unlink_failure_after_windows_chmod_can_resume(
    completed, job, package, monkeypatch
):
    bundle = prepare(completed, job, package)
    original = Path.unlink

    def fail_request(path, *args, **kwargs):
        if path == job.path / "request.json":
            raise OSError("request unlink failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_request)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert job.path.exists() and (job.path / "request.json").exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert release(bundle, job, package).state == "PREFIX_INPUTS_RELEASED"


def test_directory_removal_failure_resumes_without_recreating_any_input(
    completed, job, package, monkeypatch
):
    bundle = prepare(completed, job, package)
    original = Path.rmdir

    def fail_directory(path, *args, **kwargs):
        if path == job.path:
            raise OSError("directory removal failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rmdir", fail_directory)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert (
        job.path.exists() and not (job.path / "request.json").exists() and job.stage_path.exists()
    )
    monkeypatch.setattr(Path, "rmdir", original)
    assert release(bundle, job, package).state == "PREFIX_INPUTS_RELEASED"


def test_preparation_does_not_adopt_an_existing_archive(completed, job, package):
    bundle = prepare(completed, job, package)
    with pytest.raises(ValueError):
        prepare(completed, job, package)
    assert (
        job.path.parent / (job.path.name + ".release.json")
    ).read_bytes() == bundle.model_dump_json().encode()
    assert (job.path / "request.json").exists()


def test_final_marker_fsync_failure_restores_exact_offline_observation(
    completed, job, package, monkeypatch
):
    bundle = prepare(completed, job, package)
    original = module().os.fsync
    count = 0

    def fail_final(fd):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("final observation sync failed")
        return original(fd)

    monkeypatch.setattr(module().os, "fsync", fail_final)
    with pytest.raises(OSError):
        release(bundle, job, package)
    assert not job.path.exists()
    monkeypatch.setattr(module().os, "fsync", original)
    assert release(bundle, job, package).state == "PREFIX_INPUTS_RELEASED"


def test_reappeared_input_after_final_release_is_never_automatically_deleted(
    completed, job, package
):
    bundle = prepare(completed, job, package)
    release(bundle, job, package)
    job.path.mkdir()
    path = job.path / "request.json"
    path.write_bytes(b"keep")
    with pytest.raises(ValueError):
        release(bundle, job, package)
    assert path.read_bytes() == b"keep"


def test_foreign_release_lock_owner_prevents_any_deletion(completed, job, package):
    from ai_quant_os.root_ownership import kernel_root_lock

    bundle = prepare(completed, job, package)
    with kernel_root_lock(job.path.parent, lock_name=".strategy-owner.lock"):
        with pytest.raises(ValueError):
            release(bundle, job, package)
    assert (job.path / "request.json").exists()
