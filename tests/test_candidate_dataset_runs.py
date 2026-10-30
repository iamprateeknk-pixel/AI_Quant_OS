"""Window identity is pinned before any source execution or numeric replay."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_quant_os.strategy_prefix_run import prepare_strategy_prefix_run, run_strategy_prefixes
from test_strategy_prefix_worker import package as package

ROOT = Path(__file__).resolve().parents[1]


def contracts():
    assert importlib.util.find_spec("ai_quant_os.candidate_dataset_run_contracts"), (
        "Source-window run manifest contracts required"
    )
    return importlib.import_module("ai_quant_os.candidate_dataset_run_contracts")


def image_inputs():
    return {
        "archive": ROOT / "data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar",
        "base_metadata": ROOT / "infra/repair-worker",
        "worker_sha256": "c94e1dc2a34c31a76bd42413b16dc4b906cec123e107393156d61ef0f3cc09ee",
        "base_manifest_digest": "sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5",
        "archive_sha256": "d233bc9c8a2093d18c4392a913db5aaf18d3d27ae5838af58d339653e268f375",
        "manifest_digest": "sha256:1e0b8a64de1536ba581f7ffaead12574996f48e7385054c62f5a60826ef95e48",
        "config_digest": "sha256:415c3433e8ec2d3ac89463032e6bcad71e25f58b03632f1fe516b02c100b5cf7",
    }


def test_explicit_window_run_contracts_are_separate_from_original_holdout_wire_models():
    module = contracts()
    assert "dataset_window" in module.CandidateDatasetRunManifest.model_fields
    assert (
        "dataset_window"
        not in __import__(
            "ai_quant_os.strategy_prefix_run_contracts", fromlist=["StrategyPrefixRunManifest"]
        ).StrategyPrefixRunManifest.model_fields
    )
    assert (
        module.CandidateDatasetRunReport.model_fields["manifest"].annotation
        is module.CandidateDatasetRunManifest
    )


@pytest.mark.parametrize(
    "name,model_name",
    [
        ("candidate-dataset-window", "CandidateDatasetWindow"),
        ("candidate-dataset-run-manifest", "CandidateDatasetRunManifest"),
        ("candidate-dataset-run-report", "CandidateDatasetRunReport"),
    ],
)
def test_retained_dataset_wire_schema_matches_admitted_model(name, model_name):
    path = ROOT / "docs/schemas" / (name + ".schema.json")
    assert path.exists(), "Explicit dataset execution wire contract required"
    assert json.loads(path.read_bytes()) == getattr(contracts(), model_name).model_json_schema()


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_DATASET_WINDOW_ENGINE_TEST") != "1",
    reason="Explicit original source-window engine gate required",
)
def test_actual_development_windows_run_source_and_replay_with_pinned_identity(package, tmp_path):
    contracts()
    from ai_quant_os.candidate_test_execution import prepare_candidate_tests, run_candidate_tests
    from ai_quant_os.candidate_dataset_views import development_window

    test_root = tmp_path / "candidate-tests"
    test_root.mkdir()
    test_pin = prepare_candidate_tests(package, test_root)
    tested = run_candidate_tests(test_root, test_pin)
    completed_windows = []
    for fold in (0, 1):
        view = development_window(package, fold, tested)
        root = tmp_path / ("development-fold-" + str(fold))
        root.mkdir()
        manifest = prepare_strategy_prefix_run(view, root=root, **image_inputs())
        assert manifest.dataset_window == view.window
        assert manifest.row_count == 4
        wrong = development_window(package, 1 - fold, tested)
        with pytest.raises(ValueError):
            run_strategy_prefixes(wrong, root=root, run_sha256=manifest.run_sha256)
        report = run_strategy_prefixes(view, root=root, run_sha256=manifest.run_sha256)
        completed_windows.append(report)
        assert (
            report.manifest == manifest
            and report.status == "UNATTESTED_DEVELOPMENT_WINDOW_NATIVE_REPLAY"
        )
        assert report.replay.collection.tape.dataset_ref == package.training.receipt.dataset_ref
        assert report.replay.collection.tape.dataset_fingerprint == view.window.window_fingerprint
        assert len(report.replay.collection.receipts) == 2
        assert report.stress_replay.collection == report.replay.collection
        assert not [path for path in root.glob("strategy-job-*") if path.is_dir()]
        assert run_strategy_prefixes(view, root=root, run_sha256=manifest.run_sha256) == report
        assert report.promotion_allowed is False
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "ai_quant_os.strategy_prefix_run_cli",
                "--validation-archive",
                str(tmp_path / "service.validation-packages.sqlite3"),
                "--package-ref",
                package.package_ref,
                "--root",
                str(root),
                "--development-fold",
                str(fold),
                "--candidate-test-root",
                str(test_root),
                "--candidate-test-manifest-sha256",
                test_pin,
                "run",
                "--run-sha256",
                manifest.run_sha256,
            ],
            capture_output=True,
            timeout=30,
        )
        assert child.returncode == 0, child.stderr.decode()
        assert json.loads(child.stdout)["status"] == report.status
    from ai_quant_os.candidate_validation import evaluate_candidate, verify_candidate_validation

    holdout_root = tmp_path / "holdout"
    holdout_root.mkdir()
    manifest = prepare_strategy_prefix_run(package, root=holdout_root, **image_inputs())
    holdout = run_strategy_prefixes(package, root=holdout_root, run_sha256=manifest.run_sha256)
    scorecard = evaluate_candidate(package, tested, holdout, tuple(completed_windows))
    verify_candidate_validation(package, tested, holdout, tuple(completed_windows), scorecard)
    assert scorecard.promotion_allowed is False
    assert scorecard.monte_carlo.state == "PENDING"
    assert scorecard.monte_carlo.reason == "UNCLOSED_POSITION"
    assert len(scorecard.gates) == 15
    (tmp_path / "candidate-validation-scorecard.json").write_bytes(
        scorecard.model_dump_json().encode()
    )
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    from test_validation_package_api import existing_validation_service
    from test_research_hypothesis_api import HEADERS

    with existing_validation_service(tmp_path) as (validator_client, archive):
        assert (
            archive.put_evaluation(package, tested, holdout, tuple(completed_windows)) == scorecard
        )
        with TestClient(
            create_app(
                operator_token="fixture-hypothesis",
                paper_db_path=tmp_path / "paper.sqlite3",
                clock=lambda: datetime.now(timezone.utc),
                holdout_rpc_client=validator_client,
            )
        ) as client:
            context = client.post(
                "/api/v1/sandbox/validation-scientist/context",
                headers=HEADERS,
                json={"task_id": "actual-validation", "artifact_ref": scorecard.scorecard_ref},
            )
            assert context.status_code == 200, context.text
            response = client.post(
                "/api/v1/sandbox/validation-scientist/execute",
                headers=HEADERS,
                json={"run_id": "actual-validation", "context_ref": context.json()["context_ref"]},
            )
            assert response.status_code == 200, response.text
            receipt = response.json()["receipt"]
            assert receipt["status"] == "COMPLETED", receipt
            output = json.loads(receipt["output_json"])
            assert output["summary"]["artifact_ref"] == scorecard.scorecard_ref
            assert len(output["summary"]["gates"]) == 15
            assert output["promotion_allowed"] is False
            (tmp_path / "validation-scientist-receipt.json").write_text(
                json.dumps(receipt, sort_keys=True), encoding="utf-8"
            )
