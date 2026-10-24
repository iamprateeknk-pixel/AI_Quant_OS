import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.holdout_validator_service import HoldoutRequestHandler
from ai_quant_os.holdout_validator_rpc import HoldoutRpcClient, process_rpc_frame
from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore, ResearchEvidenceCatalog
from ai_quant_os.research_store import ResearchStore
from ai_quant_os.validation_package_store import ValidationPackageStore
from test_research_hypothesis_api import NOW, HEADERS, training_csv
from test_strategy_builder_api import review, build


@contextmanager
def validation_service_environment(tmp_path, *, holdout_raw=None, paper_db_path=None):
    with ExitStack() as stack:
        service_research = tmp_path / "service.research-data.sqlite3"
        if holdout_raw is None:
            holdout_raw = training_csv().replace("2026-10-01", "2026-10-02").encode()
        with ResearchEvidenceCatalog(service_research) as catalog:
            holdout_receipt = catalog.publish(
                holdout_raw,
                source_id="later-holdout",
                resource_class="HOLDOUT",
                received_at=NOW,
            )
        holdouts = stack.enter_context(
            HoldoutEvidenceStore(
                service_research.with_name("service.research-data.holdout.sqlite3")
            )
        )
        packages = stack.enter_context(
            ValidationPackageStore(tmp_path / "service.validation-packages.sqlite3")
        )
        validations = stack.enter_context(
            CandidateValidationStore(tmp_path / "service.candidate-validations.sqlite3")
        )
        research = ResearchStore(tmp_path / "service.research.sqlite3")
        handler = HoldoutRequestHandler(holdouts, packages, validations, research)

        class LocalServiceClient:
            def exchange(self, frame, *, timeout_ms):
                assert timeout_ms > 0
                return process_rpc_frame(frame, handler)

        service_client = HoldoutRpcClient(LocalServiceClient())

        settings = {
            "operator_token": "fixture-hypothesis",
            "clock": lambda: NOW,
            "paper_db_path": paper_db_path or tmp_path / "paper.sqlite3",
            "holdout_rpc_client": service_client,
        }
        yield settings, holdout_receipt, packages


@contextmanager
def existing_validation_service(tmp_path):
    """Reopen the package fixture's service-owned stores for direct evidence setup."""
    with ExitStack() as stack:
        holdouts = stack.enter_context(
            HoldoutEvidenceStore(tmp_path / "service.research-data.holdout.sqlite3")
        )
        packages = stack.enter_context(
            ValidationPackageStore(tmp_path / "service.validation-packages.sqlite3")
        )
        validations = stack.enter_context(
            CandidateValidationStore(tmp_path / "service.candidate-validations.sqlite3")
        )
        research = ResearchStore(tmp_path / "service.research.sqlite3")
        handler = HoldoutRequestHandler(holdouts, packages, validations, research)

        class LocalServiceClient:
            def exchange(self, frame, *, timeout_ms):
                assert timeout_ms > 0
                return process_rpc_frame(frame, handler)

        yield HoldoutRpcClient(LocalServiceClient()), validations


def prepare(client, holdout_ref):
    reviewed = review(client)
    _, _, output, candidate = build(client, reviewed)
    payload = {
        "run_id": "build-1",
        "holdout_ref": holdout_ref,
        "plan": {
            "plan_id": "direction-validation",
            "rationale": "Synthetic development check.",
            "spread_bps": "2",
            "slippage_bps": "1",
            "commission_bps": "0.5",
            "cost_stress_multiplier": "2",
            "walk_forward_train_bars": 8,
            "walk_forward_test_bars": 2,
            "monte_carlo_trials": 1000,
            "random_seed": 27,
        },
    }
    return payload, output, candidate


def test_package_retains_exact_sources_data_graph_plan_and_restarts_without_raw_http(tmp_path):
    with validation_service_environment(tmp_path) as (settings, holdout, packages):
        with TestClient(create_app(**settings)) as client:
            payload, output, candidate = prepare(client, holdout.dataset_ref)
            before = client.get("/api/v1/system/status").json()
            response = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            assert response.status_code == 200, response.text
            metadata = response.json()
            assert metadata["candidate_ref"] == output["candidate_ref"]
            assert metadata["plan"]["cost_basis"] == "OPERATOR_DECLARED_SYNTHETIC"
            assert metadata["status"] == "WAIT_FOR_ISOLATED_VALIDATOR"
            assert metadata["validation_status"] == "NOT_RUN"
            assert not metadata["execution_authorized"] and not metadata["isolation_verified"]
            assert not metadata["promotion_authorized"]
            assert "raw_base64" not in json.dumps(metadata)
            assert client.get("/api/v1/system/status").json() == before
            assert (
                client.post(
                    "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
                ).json()
                == metadata
            )
        retained = packages.get(metadata["package_ref"])
        assert retained is not None
        assert retained.candidate.model_dump(mode="json") == candidate
        assert retained.graph.receipt.run_id == "build-1"
        assert (
            retained.training.receipt.dataset_ref
            == candidate["review"]["hypothesis"]["dataset_ref"]
        )
        assert (
            base64.b64decode(retained.holdout.raw_base64)
            == training_csv().replace("2026-10-01", "2026-10-02").encode()
        )
        root = Path(__file__).resolve().parents[1] / "src/ai_quant_os"
        assert [row.path for row in retained.sdk_files] == [
            "ai_quant_os/__init__.py",
            "ai_quant_os/contracts.py",
            "ai_quant_os/market_data.py",
            "ai_quant_os/strategy_contracts.py",
        ]
        for row in retained.sdk_files:
            raw = base64.b64decode(row.raw_base64)
            assert raw == (root / Path(row.path).name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row.sha256
        with TestClient(create_app(**settings)) as client:
            response = client.get(
                "/api/v1/sandbox/validation/packages/" + metadata["package_ref"],
                headers=HEADERS,
            )
            assert response.status_code == 200 and response.json() == metadata
        assert list(tmp_path.rglob("candidate.py")) == []
        assert list(tmp_path.rglob("test_candidate.py")) == []


def test_package_plan_cannot_change_under_existing_identity(tmp_path):
    with validation_service_environment(tmp_path) as (settings, holdout, _packages):
        with TestClient(create_app(**settings)) as client:
            payload, _, _ = prepare(client, holdout.dataset_ref)
            first = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            assert first.status_code == 200, first.text
            payload["plan"]["random_seed"] = 28
            rejected = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            assert rejected.status_code == 409, rejected.text
            assert (
                client.get(
                    "/api/v1/sandbox/validation/packages/" + first.json()["package_ref"],
                    headers=HEADERS,
                ).json()
                == first.json()
            )


def test_package_routes_require_operator_and_reject_client_authority(tmp_path):
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/sandbox/validation/packages", json={}).status_code == 503
    with validation_service_environment(tmp_path) as (settings, holdout, _packages):
        with TestClient(create_app(**settings)) as client:
            payload, _, _ = prepare(client, holdout.dataset_ref)
            assert (
                client.post("/api/v1/sandbox/validation/packages", json=payload).status_code == 401
            )
            assert (
                client.post(
                    "/api/v1/sandbox/validation/packages",
                    headers=HEADERS,
                    json={**payload, "execution_authorized": True},
                ).status_code
                == 422
            )


@pytest.mark.parametrize("change", ["training_label", "wrong_graph", "bad_costs"])
def test_package_refuses_wrong_classification_graph_and_invalid_cost_plan(tmp_path, change):
    with validation_service_environment(tmp_path) as (settings, holdout, _packages):
        with TestClient(create_app(**settings)) as client:
            payload, _, candidate = prepare(client, holdout.dataset_ref)
            if change == "training_label":
                payload["holdout_ref"] = candidate["review"]["hypothesis"]["dataset_ref"]
            elif change == "wrong_graph":
                payload["run_id"] = "research-1"
            else:
                payload["plan"]["spread_bps"] = "NaN"
            response = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            expected_status = 422 if change == "bad_costs" else 409
            assert response.status_code == expected_status, response.text
