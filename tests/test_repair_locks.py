"""Trusted fixture processes prove local kernel ownership; no candidate runs."""

import importlib
import os
import subprocess
import sys
from queue import Queue
from threading import Thread
from pathlib import Path

import pytest

from ai_quant_os.repair_jobs import prepared_repair_job
from test_repair_jobs import PROGRAM, SUITE_SHA, snapshot
from test_repair_workspace import repository as repository


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_locks") is not None
    return importlib.import_module("ai_quant_os.repair_locks")


def command(root, *, hold=False):
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = (
        "import sys;sys.path.insert(0,"
        + repr(source)
        + ");from ai_quant_os.repair_locks import repair_root_lock\nwith repair_root_lock("
        + repr(str(root))
        + "):\n print('owned',flush=True)\n"
        + (" sys.stdin.buffer.read(1)\n" if hold else "")
    )
    return [sys.executable, "-I", "-c", script]


def test_actual_other_process_is_denied_then_parent_death_releases_kernel_lock(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    module()
    child = subprocess.Popen(
        command(root, hold=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready = Queue()
        Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
        assert ready.get(timeout=5).strip() == b"owned"
        competitor = subprocess.run(command(root), capture_output=True, timeout=5)
        assert competitor.returncode != 0
        assert not competitor.stdout
        child.kill()
        child.wait(timeout=5)
        successor = subprocess.run(command(root), capture_output=True, timeout=5)
        assert successor.returncode == 0, successor.stderr
        assert successor.stdout.strip() == b"owned"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_same_process_second_owner_refused_and_different_roots_independent(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with module().repair_root_lock(first):
        with pytest.raises((ValueError, OSError)):
            with module().repair_root_lock(first):
                pytest.fail("Second owner admitted")
        with module().repair_root_lock(second):
            pass
    with module().repair_root_lock(first):
        pass


def test_prepared_job_holds_root_for_its_entire_consumer_lifetime(tmp_path, repository):
    module()
    root = tmp_path / "jobs"
    with prepared_repair_job(
        snapshot(tmp_path, repository), PROGRAM, suite_sha256=SUITE_SHA, root=root
    ):
        blocked = subprocess.run(command(root), capture_output=True, timeout=5)
        assert blocked.returncode != 0
        assert not blocked.stdout
    released = subprocess.run(command(root), capture_output=True, timeout=5)
    assert released.returncode == 0, released.stderr


@pytest.mark.parametrize("kind", ["hardlink", "directory", "contents"])
def test_lock_file_with_invalid_identity_or_contents_is_denied(tmp_path, kind):
    root = tmp_path / "jobs"
    root.mkdir()
    path = root / ".repair-owner.lock"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"x" if kind == "contents" else b"\x00")
        if kind == "hardlink":
            os.link(path, tmp_path / "outside-lock")
    with pytest.raises((ValueError, OSError)):
        with module().repair_root_lock(root):
            pytest.fail("Invalid lock admitted")


def test_missing_root_is_not_created_and_exception_releases_owner(tmp_path):
    root = tmp_path / "missing"
    with pytest.raises(ValueError):
        with module().repair_root_lock(root):
            pass
    assert not root.exists()
    root.mkdir()
    with pytest.raises(RuntimeError, match="consumer"):
        with module().repair_root_lock(root):
            raise RuntimeError("consumer failure")
    with module().repair_root_lock(root):
        pass


def test_reconstruction_and_cleanup_refuse_live_root_owner_before_daemon_access(
    tmp_path, repository, monkeypatch
):
    from test_repair_cleanup import retained, read
    from test_repair_containers import module as containers
    from ai_quant_os.repair_sandbox import DockerConfinement
    from test_repair_sandbox import IMAGE

    evidence, path, lease, fixture = retained(tmp_path, repository, monkeypatch)
    job = read(evidence, lease, tmp_path / "jobs")
    fixture.cleanup_error = False
    fixture.calls.clear()
    with module().repair_root_lock(tmp_path / "jobs"):
        with pytest.raises(ValueError, match="owned"):
            read(evidence, lease, tmp_path / "jobs")
        with pytest.raises(ValueError, match="owned"):
            containers().cleanup_retained_repair_container(DockerConfinement(IMAGE, SUITE_SHA), job)
    assert fixture.calls == []
    assert path.exists() and lease.exists()
