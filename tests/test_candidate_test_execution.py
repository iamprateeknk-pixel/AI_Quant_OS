"""One durable source-only job runs in the fixed Linux dependency image."""

from hashlib import sha256
import importlib
import json
import os
import subprocess
import sys

import pytest

from test_strategy_prefix_worker import package as package


def controller():
    assert importlib.util.find_spec("ai_quant_os.candidate_test_execution"), (
        "Durable candidate test controller required"
    )
    return importlib.import_module("ai_quant_os.candidate_test_execution")


def test_prepare_pins_source_only_inputs_and_native_root(package, tmp_path):
    root = tmp_path / "candidate-tests"
    root.mkdir()
    pin = controller().prepare_candidate_tests(package, root)
    raw = (root / "manifest.json").read_bytes()
    assert pin == sha256(raw).hexdigest()
    value = json.loads(raw)
    assert value["package_ref"] == package.package_ref
    assert value["promotion_allowed"] is False
    assert {row.name for row in (root / "job").iterdir()} == {
        "request.json",
        "runtime_guard.py",
        "verifier.py",
    }
    assert package.holdout.raw_base64 not in (root / "job/request.json").read_text()
    with pytest.raises(ValueError):
        controller().prepare_candidate_tests(package, root)


@pytest.mark.parametrize("change", ["pin", "source", "extra", "root"])
def test_changed_staged_input_refuses_before_docker(package, tmp_path, monkeypatch, change):
    module = controller()
    root = tmp_path / "candidate-tests"
    root.mkdir()
    pin = module.prepare_candidate_tests(package, root)
    if change == "pin":
        pin = "0" * 64
    elif change == "source":
        source = root / "job/request.json"
        source.chmod(0o644)
        source.write_bytes(b"{}")
    elif change == "extra":
        (root / "job/holdout.csv").write_bytes(b"forbidden")
    else:
        root.rename(tmp_path / "original-tests")
        root.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("Changed staged inputs must refuse before Docker access")

    monkeypatch.setattr(module.DockerConfinement, "probe", forbidden)
    with pytest.raises(ValueError):
        module.run_candidate_tests(root, pin)


def test_durable_create_intent_refuses_repeated_execution(package, tmp_path, monkeypatch):
    module = controller()
    root = tmp_path / "candidate-tests"
    root.mkdir()
    pin = module.prepare_candidate_tests(package, root)
    (root / "create-intent.json").write_text("{}")

    def forbidden(*args, **kwargs):
        pytest.fail("Persisted intent must prevent another create")

    monkeypatch.setattr(module.DockerConfinement, "probe", forbidden)
    with pytest.raises(ValueError, match="intent"):
        module.run_candidate_tests(root, pin)


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_CANDIDATE_TEST_ENGINE_TEST") != "1",
    reason="Explicit real-engine candidate test gate required",
)
def test_actual_six_proposed_tests_compile_and_run_once(package, tmp_path):
    module = controller()
    root = tmp_path / "candidate-tests"
    root.mkdir()
    pin = module.prepare_candidate_tests(package, root)
    result = module.run_candidate_tests(root, pin)
    assert result.status == "PROPOSED_TESTS_PASSED_UNATTESTED"
    assert (
        result.compiled_files,
        result.collected,
        result.passed,
        result.failed,
        result.skipped,
    ) == (6, 6, 6, 0, 0)
    assert result.promotion_allowed is False and result.isolation_verified is False
    assert (root / "removed.json").is_file()
    assert module.run_candidate_tests(root, pin) == result
    assert module.read_candidate_test_result(root, pin) == result
    assert len(list(root.glob("start-intent.json"))) == 1
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.candidate_test_cli",
            "--root",
            str(root),
            "result",
            "--manifest-sha256",
            pin,
        ],
        capture_output=True,
        timeout=20,
    )
    assert child.returncode == 0 and json.loads(child.stdout) == result.model_dump(mode="json")


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_CANDIDATE_TEST_ENGINE_TEST") != "1",
    reason="Explicit real-engine candidate test gate required",
)
def test_lost_start_ack_keeps_intent_and_prevents_repeat(package, tmp_path, monkeypatch):
    module = controller()
    root = tmp_path / "candidate-tests"
    root.mkdir()
    pin = module.prepare_candidate_tests(package, root)
    real = module.local_docker_command

    def lose_ack(*args, **kwargs):
        result = real(*args, **kwargs)
        assert result.returncode == 0
        raise TimeoutError("Simulated lost acknowledgement after actual worker exit")

    monkeypatch.setattr(module, "local_docker_command", lose_ack)
    with pytest.raises(TimeoutError):
        module.run_candidate_tests(root, pin)
    assert (root / "start-intent.json").is_file() and (root / "removed.json").is_file()
    assert not (root / "result.json").exists()
    with pytest.raises(ValueError, match="intent"):
        module.run_candidate_tests(root, pin)
    assert (
        module.cleanup_candidate_tests(root, pin)
        == json.loads((root / "removed.json").read_bytes())["container_id"]
    )
