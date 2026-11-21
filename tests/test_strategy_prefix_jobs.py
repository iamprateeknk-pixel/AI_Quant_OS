"""Host stages inert JSON only; no candidate import, compilation or execution."""

from hashlib import sha256
from datetime import timedelta
import importlib
import json
import os
import subprocess
import sys

import pytest

from ai_quant_os.candidate_invocations import prepare_invocation
from ai_quant_os.root_ownership import kernel_root_lock
from test_strategy_prefix_worker import package as package


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_prefix_jobs"), (
        "Owned prefix request staging is required"
    )
    return importlib.import_module("ai_quant_os.strategy_prefix_jobs")


@pytest.fixture
def root(tmp_path):
    value = tmp_path / "jobs"
    value.mkdir()
    return value


def test_stage_contains_only_exact_package_bound_request_and_separate_metadata(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    request = prepare_invocation(package, 2)
    assert job.path.parent == root.resolve()
    assert [path.name for path in job.path.iterdir()] == ["request.json"]
    assert (job.path / "request.json").read_bytes() == request.model_dump_json().encode()
    assert job.invocation_ref == request.invocation_ref
    assert job.stage_sha256 == sha256(job.stage_path.read_bytes()).hexdigest()
    assert job.path.name.startswith("strategy-job-")
    job.verify(package)
    for path in job.path.rglob("*"):
        assert path.suffix != ".py"
    stage = json.loads(job.stage_path.read_bytes())
    assert stage["execution_verified"] is False and stage["promotion_allowed"] is False
    assert not {"source", "sdk_files", "history", "training", "holdout"} & stage.keys()


def test_independent_stage_pin_restores_and_rechecks_original_package(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    restored = module().load_prefix_job(
        package, 2, root=root, job_name=job.path.name, stage_sha256=job.stage_sha256
    )
    assert restored.path == job.path and restored.invocation_ref == job.invocation_ref
    restored.verify(package)


@pytest.mark.parametrize(
    "damage", ["bytes", "extra", "missing", "hardlink", "directory", "receipt", "mode"]
)
def test_changed_staging_is_refused_and_retained(package, root, damage):
    job = module().stage_prefix_job(package, 2, root=root)
    request = job.path / "request.json"
    if damage == "bytes":
        request.chmod(0o644)
        request.write_bytes(b"changed")
    elif damage == "extra":
        (job.path / "unexpected.txt").write_text("retained")
    elif damage == "missing":
        request.chmod(0o644)
        request.unlink()
    elif damage == "hardlink":
        os.link(request, root / "external-alias.json")
    elif damage == "directory":
        request.chmod(0o644)
        request.unlink()
        request.mkdir()
    elif damage == "receipt":
        job.stage_path.write_bytes(b"changed")
    else:
        request.chmod(0o666)
    with pytest.raises((ValueError, OSError)):
        job.verify(package)
    with pytest.raises((ValueError, OSError)):
        job.discard_unstarted(package)
    assert job.path.exists()


def test_replaced_request_with_identical_bytes_is_refused_by_identity(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    request = job.path / "request.json"
    raw = request.read_bytes()
    request.chmod(0o644)
    request.rename(job.path / "original.json")
    request.write_bytes(raw)
    request.chmod(0o444)
    (job.path / "original.json").unlink()
    with pytest.raises(ValueError):
        job.verify(package)


def test_replaced_directory_is_not_deleted_even_with_identical_request(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    raw = (job.path / "request.json").read_bytes()
    job.path.rename(root / "original-job")
    job.path.mkdir()
    (job.path / "request.json").write_bytes(raw)
    (job.path / "request.json").chmod(0o444)
    with pytest.raises(ValueError):
        job.discard_unstarted(package)
    assert job.path.exists() and (root / "original-job").exists()


def test_changed_parent_directory_is_refused(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    root.rename(root.with_name("old-jobs"))
    root.mkdir()
    with pytest.raises((ValueError, OSError)):
        job.verify(package)
    assert root.with_name("old-jobs").exists()
    assert list(root.iterdir()) == [], "Changed parents must be refused before opening a lock there"


@pytest.mark.parametrize(
    "field", ["root_inode", "request_inode", "invocation_ref", "execution_verified"]
)
def test_rehashed_stage_metadata_cannot_bypass_independent_original_pin(package, root, field):
    job = module().stage_prefix_job(package, 2, root=root)
    payload = json.loads(job.stage_path.read_bytes())
    payload[field] = True if field == "execution_verified" else "forged"
    job.stage_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        module().load_prefix_job(
            package, 2, root=root, job_name=job.path.name, stage_sha256=job.stage_sha256
        )


@pytest.mark.parametrize("index", [True, 0, 1, 4096, "2"])
def test_invalid_index_refuses_before_creating_job_or_lock(package, root, index):
    with pytest.raises(ValueError):
        module().stage_prefix_job(package, index, root=root)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["../foreign", "strategy-job-../foreign", "/tmp/foreign", "strategy-job-a", ""]
)
def test_untrusted_restore_name_is_refused(package, root, name):
    with pytest.raises(ValueError):
        module().load_prefix_job(package, 2, root=root, job_name=name, stage_sha256="0" * 64)


def test_exact_unstarted_cleanup_preserves_neighbors_and_permanent_lock(package, root):
    first = module().stage_prefix_job(package, 2, root=root)
    second = module().stage_prefix_job(package, 3, root=root)
    first.discard_unstarted(package)
    assert not first.path.exists() and not first.stage_path.exists()
    second.verify(package)
    assert (root / ".strategy-owner.lock").is_file()


def test_future_container_lease_or_unknown_job_sidecar_prevents_input_cleanup(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    (root / (job.path.name + ".container.json")).write_text("pending create")
    with pytest.raises(ValueError):
        job.discard_unstarted(package)
    assert job.path.exists()


def test_kernel_owner_prevents_concurrent_stage_or_cleanup(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    with kernel_root_lock(root, lock_name=".strategy-owner.lock"):
        with pytest.raises(ValueError):
            module().stage_prefix_job(package, 3, root=root)
        with pytest.raises(ValueError):
            job.discard_unstarted(package)
    job.verify(package)


def test_wrong_package_prefix_cannot_verify_restore_or_delete_job(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    with pytest.raises(ValueError):
        module().load_prefix_job(
            package, 3, root=root, job_name=job.path.name, stage_sha256=job.stage_sha256
        )
    assert job.path.exists()


def test_capacity_limit_preserves_all_retained_jobs(package, root):
    for index in range(32):
        (root / ("strategy-job-" + f"{index:032x}")).mkdir()
    with pytest.raises(ValueError):
        module().stage_prefix_job(package, 2, root=root)
    assert sum(path.is_dir() for path in root.iterdir()) == 32


def test_restore_refuses_copied_receipt_in_replaced_parent_before_creating_lock(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    raw = job.stage_path.read_bytes()
    root.rename(root.with_name("old-root"))
    root.mkdir()
    (root / job.stage_path.name).write_bytes(raw)
    with pytest.raises(ValueError):
        module().load_prefix_job(
            package, 2, root=root, job_name=job.path.name, stage_sha256=job.stage_sha256
        )
    assert {path.name for path in root.iterdir()} == {job.stage_path.name}


def cli(package, root, command, *extra, archive=None):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.strategy_prefix_cli",
            "--archive",
            str(archive or root.parent / "service.validation-packages.sqlite3"),
            "--package-ref",
            package.package_ref,
            "--index",
            "2",
            "--root",
            str(root),
            command,
            *extra,
        ],
        capture_output=True,
        timeout=30,
    )


def test_operator_cli_stages_inspects_and_discards_exact_unstarted_json(package, root):
    staged = cli(package, root, "stage")
    assert staged.returncode == 0, staged.stderr
    receipt = json.loads(staged.stdout)
    assert receipt["state"] == "PREFIX_STAGED" and receipt["execution_verified"] is False
    job = root / receipt["job_name"]
    assert {path.name for path in job.iterdir()} == {"request.json"}
    pins = ["--job-name", receipt["job_name"], "--stage-sha256", receipt["stage_sha256"]]
    inspected = cli(package, root, "inspect", *pins)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["state"] == "PREFIX_STAGED_INSPECTED"
    discarded = cli(package, root, "discard", *pins)
    assert discarded.returncode == 0, discarded.stderr
    assert json.loads(discarded.stdout)["state"] == "UNSTARTED_PREFIX_DISCARDED"
    assert not job.exists()


def test_operator_cli_missing_archive_refuses_without_creating_any_job(package, root):
    result = cli(package, root, "stage", archive=root.parent / "missing.sqlite3")
    assert result.returncode == 2
    assert result.stdout == b"" and b"Traceback" not in result.stderr
    assert list(root.iterdir()) == []


def test_operator_cli_wrong_pin_retains_request_and_does_not_echo_source(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    result = cli(package, root, "discard", "--job-name", job.path.name, "--stage-sha256", "0" * 64)
    assert result.returncode == 2 and b"Traceback" not in result.stderr
    assert b"candidate.py" not in result.stderr and result.stdout == b""
    job.verify(package)


def test_distinct_valid_package_identity_cannot_verify_or_delete_original_job(package, root):
    job = module().stage_prefix_job(package, 2, root=root)
    changed = package.model_copy(update={"captured_at": package.captured_at + timedelta(seconds=1)})
    changed = type(package).model_validate_json(changed.model_dump_json())
    assert changed.package_ref != package.package_ref
    with pytest.raises(ValueError):
        job.verify(changed)
    with pytest.raises(ValueError):
        job.discard_unstarted(changed)
    job.verify(package)


def test_reparse_directory_metadata_is_refused_before_job_creation(package, root, monkeypatch):
    from types import SimpleNamespace

    implementation = module()
    original = implementation.Path.lstat

    def reparse(path):
        metadata = original(path)
        if path == root:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=1024,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
            )
        return metadata

    monkeypatch.setattr(implementation.Path, "lstat", reparse)
    with pytest.raises(ValueError):
        implementation.stage_prefix_job(package, 2, root=root)
    assert list(root.iterdir()) == []
