import base64
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
import sqlite3
import json
from pathlib import Path

from fastapi.testclient import TestClient
import jsonschema
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.validation_package_contracts import SDKFile, ValidationPackage
from ai_quant_os.validation_package_service import ValidationPreparation
from ai_quant_os.validation_package_store import ValidationPackageStore
from test_research_hypothesis_api import NOW, HEADERS
from test_validation_package_api import prepare, validation_service_environment


@pytest.fixture
def retained(tmp_path):
    with validation_service_environment(tmp_path) as (settings, holdout, packages):
        with TestClient(create_app(**settings)) as client:
            payload, _, _ = prepare(client, holdout.dataset_ref)
            response = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            assert response.status_code == 200, response.text
        package = packages.get(response.json()["package_ref"])
    return tmp_path / "service.validation-packages.sqlite3", package


def test_invalid_trusted_sdk_syntax_is_a_typed_rejection_without_execution():
    raw = b"this is not python ("
    with pytest.raises(ValueError, match="SDK"):
        SDKFile(
            path="ai_quant_os/contracts.py",
            raw_base64=base64.b64encode(raw).decode(),
            sha256=sha256(raw).hexdigest(),
        )


def test_preparation_refuses_naive_capture_time_instead_of_assuming_host_timezone(retained):
    _, package = retained

    class NoReads:
        def get_candidate(self, *_):
            pytest.fail("Naive clock must be rejected before archive reads")

    service = ValidationPreparation(NoReads(), None)
    with pytest.raises(ValueError, match="timezone"):
        service.prepare_request(
            None,
            "research-data:" + "0" * 64,
            None,
            now=NOW.replace(tzinfo=None),
        )


def test_preparation_request_reads_training_only_and_sends_no_holdout_bytes(tmp_path):
    import base64
    import json

    from fastapi.testclient import TestClient

    from ai_quant_os.api import create_app
    from ai_quant_os.graph_ledger import GraphLedger
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
    from ai_quant_os.strategy_build_store import StrategyBuildStore
    from ai_quant_os.validation_package_contracts import ValidationPlan
    from test_research_hypothesis_api import training_csv
    from test_strategy_builder_api import build, review

    paper_db = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(operator_token="fixture-hypothesis", clock=lambda: NOW, paper_db_path=paper_db)
    ) as client:
        reviewed = review(client)
        _, _, output, _ = build(client, reviewed, task="validation-build")
    with GraphLedger.archive(paper_db.with_name("paper.agents.sqlite3")) as archive:
        graph = archive.get("validation-build")
    with (
        StrategyBuildStore(paper_db.with_name("paper.strategy-builds.sqlite3")) as builds,
        ResearchEvidenceCatalog(
            paper_db.with_name("paper.research-data.sqlite3"), training_only=True
        ) as catalog,
    ):
        request = ValidationPreparation(builds, catalog).prepare_request(
            graph,
            "research-data:" + "a" * 64,
            ValidationPlan(
                plan_id="direction-validation",
                rationale="Synthetic development check.",
                spread_bps=2,
                slippage_bps=1,
                commission_bps="0.5",
                cost_stress_multiplier=2,
                walk_forward_train_bars=8,
                walk_forward_test_bars=2,
                monte_carlo_trials=1000,
                random_seed=27,
            ),
            now=NOW,
        )

    assert request.operation == "VALIDATION_PREPARE"
    assert request.evidence_ref == "validation-build"
    assert request.candidate_ref == output["candidate_ref"]
    candidate = json.loads(request.preparation.candidate_json)
    assert request.training_ref == candidate["review"]["hypothesis"]["dataset_ref"]
    assert request.preparation is not None
    assert base64.b64decode(request.preparation.training_raw_base64) == training_csv().encode()
    assert "holdout_raw" not in request.preparation.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        "sdk_hash",
        "raw_data",
        "graph_run",
        "method_omission",
        "training_binding",
        "too_many_windows",
        "future_receipt",
    ],
)
def test_retained_package_rejects_input_substitution(retained, change):
    _, package = retained
    body = package.model_dump(mode="json")
    if change == "sdk_hash":
        body["sdk_files"][2]["sha256"] = "0" * 64
    elif change == "raw_data":
        body["holdout"]["raw_base64"] = body["training"]["raw_base64"]
    elif change == "graph_run":
        body["graph"]["receipt"]["run_id"] = "other-builder"
        # Run id is valid structurally but the archived envelope/task recipe must still match.
        body["graph"]["receipt"]["context_ref"] = "agent-context:" + "0" * 64
    elif change == "method_omission":
        body["plan"]["planned_methods"] = ["OUT_OF_SAMPLE"]
    elif change == "training_binding":
        body["training"]["receipt"]["dataset_fingerprint"] = "0" * 64
    elif change == "too_many_windows":
        body["plan"]["walk_forward_train_bars"] = 20
    else:
        body["captured_at"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        ValidationPackage.model_validate(body)


@pytest.mark.parametrize("damage", ["package", "deleted_package", "deleted_event", "metadata"])
def test_package_archive_refuses_corruption_without_repair(retained, damage):
    path, _ = retained
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "package":
            db.execute("UPDATE validation_packages SET package_json='{}'")
        elif damage == "deleted_package":
            db.execute("DELETE FROM validation_packages")
        elif damage == "deleted_event":
            db.execute("DELETE FROM audit_events")
        else:
            db.execute("DROP TABLE audit_head")
    with pytest.raises(ValueError):
        ValidationPackageStore(path)


def test_archive_revalidates_model_copy_and_enforces_capacity_and_plan_identity(retained, tmp_path):
    _, package = retained
    with ValidationPackageStore(tmp_path / "bounded.sqlite3", capacity=1) as archive:
        assert archive.put(package) == package
        retry = package.model_copy(update={"captured_at": NOW + timedelta(seconds=1)})
        assert archive.put(retry) == package
        with pytest.raises(ValueError):
            archive.put(package.model_copy(update={"execution_authorized": True}))
        changed_plan = package.plan.model_copy(update={"random_seed": 28})
        with pytest.raises(ValueError, match="plan identity"):
            archive.put(package.model_copy(update={"plan": changed_plan}))
        second_plan = changed_plan.model_copy(update={"plan_id": "second-plan"})
        with pytest.raises(ValueError, match="capacity"):
            archive.put(package.model_copy(update={"plan": second_plan}))
        assert archive.get(package.package_ref) == package


def test_existing_empty_archive_is_not_reinitialized(tmp_path):
    path = tmp_path / "empty.sqlite3"
    path.touch()
    with pytest.raises(ValueError, match="metadata"):
        ValidationPackageStore(path)


def test_two_archive_connections_publish_one_identical_receipt(retained, tmp_path):
    _, package = retained
    path = tmp_path / "concurrent.sqlite3"
    with ValidationPackageStore(path) as first, ValidationPackageStore(path) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [
                pool.submit(first.put, package),
                pool.submit(
                    second.put,
                    package.model_copy(update={"captured_at": NOW + timedelta(seconds=1)}),
                ),
            ]
            results = [task.result() for task in tasks]
        assert results[0] == results[1]
        assert first.get(results[0].package_ref) == second.get(results[0].package_ref)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM validation_packages").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1


@pytest.mark.parametrize("raw", [b"import os\n", b"from . import contracts\n"])
def test_sdk_capture_rejects_dependencies_outside_declared_closure(raw):
    with pytest.raises(ValueError, match="SDK"):
        SDKFile(
            path="ai_quant_os/contracts.py",
            raw_base64=base64.b64encode(raw).decode(),
            sha256=sha256(raw).hexdigest(),
        )


def test_retained_wire_schemas_accept_actual_package_and_refuse_execution_authority(retained):
    _, package = retained
    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    schema = json.loads((root / "validation-package.schema.json").read_text())
    jsonschema.validate(package.model_dump(mode="json"), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**package.model_dump(mode="json"), "execution_authorized": True}, schema
        )
    plan_schema = json.loads((root / "validation-plan.schema.json").read_text())
    jsonschema.validate(package.plan.model_dump(mode="json"), plan_schema)
