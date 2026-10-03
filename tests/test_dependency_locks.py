"""Source-declaration drift must refuse a stale dependency lock before resolution."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


def script():
    path = Path(__file__).resolve().parents[1] / "scripts/lock_dependencies.py"
    assert path.exists(), "Missing application dependency lock workflow"
    specification = importlib.util.spec_from_file_location("dependency_lock_workflow", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def project(root, requirement="httpx>=0.27,<1", entry="first"):
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=70"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="fixture"\nversion="1.0.0"\nrequires-python=">=3.11"\n'
        f'dependencies=["{requirement}"]\n[project.optional-dependencies]\ndev=["pytest>=8,<9"]\n'
        f'[project.scripts]\nfixture="fixture:{entry}"\n',
        encoding="utf-8",
    )


def test_dependency_change_refuses_stale_lock_without_modifying_bytes(tmp_path):
    module = script()
    project(tmp_path)
    lock = tmp_path / "requirements/windows-py311.lock"
    lock.parent.mkdir()
    original = (
        module.header(tmp_path, "windows") + b"httpx==0.28.1 --hash=sha256:" + b"a" * 64 + b"\n"
    )
    lock.write_bytes(original)
    project(tmp_path, "httpx>=0.28,<1")
    with pytest.raises(ValueError, match="declaration"):
        module.compile_profile(tmp_path, "windows", check=True)
    assert lock.read_bytes() == original


def test_application_entry_changes_do_not_change_dependency_identity(tmp_path):
    module = script()
    project(tmp_path)
    original = module.header(tmp_path, "linux")
    project(tmp_path, entry="second")
    assert module.header(tmp_path, "linux") == original
    assert module.header(tmp_path, "windows") != original


def test_missing_lock_check_refuses_without_creating_directory(tmp_path):
    module = script()
    project(tmp_path)
    with pytest.raises(ValueError):
        module.compile_profile(tmp_path, "linux", check=True)
    assert not (tmp_path / "requirements").exists()


@pytest.mark.parametrize("profile", ["macos", "../outside", "", None])
def test_unknown_target_refuses_before_any_output(tmp_path, profile):
    module = script()
    project(tmp_path)
    with pytest.raises(ValueError):
        module.compile_profile(tmp_path, profile, check=False)
    assert not (tmp_path / "requirements").exists()


def test_resolution_failure_preserves_existing_lock(tmp_path, monkeypatch):
    module = script()
    project(tmp_path)
    output = tmp_path / "requirements/windows-py311.lock"
    output.parent.mkdir()
    original = module.header(tmp_path, "windows") + b"retained bytes\n"
    output.write_bytes(original)
    monkeypatch.setattr(module.shutil, "which", lambda name: "trusted-compiler")

    def fail_resolution(command, **kwargs):
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, b"uv 0.11.29\n")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", fail_resolution)
    with pytest.raises(subprocess.CalledProcessError):
        module.compile_profile(tmp_path, "windows", check=False)
    assert output.read_bytes() == original
    assert list(output.parent.iterdir()) == [output]


def test_resolution_check_refuses_changed_pins_without_publishing(tmp_path, monkeypatch):
    module = script()
    project(tmp_path)
    output = tmp_path / "requirements/linux-py311.lock"
    output.parent.mkdir()
    original = module.header(tmp_path, "linux") + b"httpx==0.28.0\n"
    output.write_bytes(original)
    monkeypatch.setattr(module.shutil, "which", lambda name: "trusted-compiler")

    def resolve(command, **kwargs):
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, b"uv 0.11.29\n")
        Path(command[command.index("--output-file") + 1]).write_bytes(b"httpx==0.28.1\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", resolve)
    with pytest.raises(ValueError, match="differ"):
        module.compile_profile(tmp_path, "linux", check=True)
    assert output.read_bytes() == original


def test_generation_uses_fixed_target_binary_policy_without_runtime_download(tmp_path, monkeypatch):
    module = script()
    project(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: "trusted-compiler")
    monkeypatch.setenv("UV_INDEX_URL", "https://untrusted.invalid")
    invocations = []

    def resolve(command, **kwargs):
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, b"uv 0.11.29\n")
        invocations.append((command, kwargs))
        Path(command[command.index("--output-file") + 1]).write_bytes(b"httpx==0.28.1\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", resolve)
    module.compile_profile(tmp_path, "linux", check=False)
    command, options = invocations[0]
    assert "--no-python-downloads" in command
    assert "--all-extras" in command
    assert command[command.index("--only-binary") + 1] == ":all:"
    assert command[command.index("--python-platform") + 1] == "x86_64-manylinux_2_35"
    assert command[command.index("--default-index") + 1] == "https://pypi.org/simple"
    assert "UV_INDEX_URL" not in options["env"]
    assert (tmp_path / "requirements/linux-py311.lock").read_bytes() == (
        module.header(tmp_path, "linux") + b"httpx==0.28.1\n"
    )
