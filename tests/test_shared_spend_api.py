"""One authenticated original-book snapshot across actual configured model routes."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.jev_adapter import JevClassifier
from ai_quant_os.openai_reviewer import OpenAIReviewer
from ai_quant_os.provider_call_admission import ProviderOwner
from ai_quant_os.provider_spend_store import ProviderSpendStore
from ai_quant_os.shared_spend_api import SharedSpendObservation
from test_shared_provider_spend import policy, reserve, JEV_MODEL, OPENAI_MODEL

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}
PATH = "/api/v1/providers/shared-spending"


def sample(tmp_path, *, second_path=None):
    store = ProviderSpendStore(tmp_path / "shared.db", policy())
    reserve(store, JEV_MODEL, now=NOW)
    known = reserve(store, now=NOW)
    store.settle(known, input_tokens=100, output_tokens=20, now=NOW)
    other = ProviderSpendStore(second_path or store.path, policy() if second_path else None)
    owner = ProviderOwner("A02", "0.2.0")
    transport = httpx.MockTransport(lambda request: pytest.fail("Read cannot call a provider"))
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=OPENAI_MODEL,
        spend_store=store,
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=transport,
    )
    classifier = JevClassifier(
        api_key="fixture-key",
        model=JEV_MODEL,
        spend_store=other,
        spend_owner=owner,
        utc_clock=lambda: NOW,
        transport=transport,
    )
    app = create_app(
        operator_token="fixture-token",
        clock=lambda: NOW,
        deep_reviewer=reviewer,
        fast_classifier=classifier,
        review_owner=owner,
        classification_owner=owner,
    )
    return store, other, reviewer, classifier, app


def test_protected_single_head_models_and_aggregate_are_exact_and_not_double_counted(tmp_path):
    store, _, _, _, app = sample(tmp_path)
    before = store.status(NOW)["head_hash"]
    with TestClient(app) as client:
        assert client.get(PATH).status_code == 401
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        SharedSpendObservation.model_validate(value)
        assert value["state"] == "ENFORCED" and len(value["agents"]) == 18
        assert len(value["models"]) == 2 and len(value["routes"]) == 2
        row = value["agents"][1]
        assert row["daily_tokens"] == 65656 and row["daily_nano_usd"] == "2752872"
        assert row["pending_count"] == 1 and row["unsettled_tokens"] == 65536
        assert row["unsettled_nano_usd"] == "2752512"
        assert value["head_hash"] == before
        assert value["invoice_verified"] is False and value["recovery_spending_enabled"] is False
        assert "fixture-key" not in response.text and str(tmp_path) not in response.text
        assert "rate_reference" not in response.text and "request_sha256" not in response.text
        assert (
            client.get("/api/v1/providers/review-spending", headers=HEADERS).json()["state"]
            == "UNAVAILABLE"
        )
    assert store.status(NOW)["head_hash"] == before


def test_separate_configured_roots_with_equal_policy_do_not_form_a_fake_shared_budget(tmp_path):
    store, other, _, _, app = sample(tmp_path, second_path=tmp_path / "separate.db")
    heads = [store.status(NOW)["head_hash"], other.status(NOW)["head_hash"]]
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
        assert value["state"] == "SEPARATE_LEDGERS"
        assert value["agents"] == value["models"] == value["routes"] == []
        assert value["book_id"] is None and value["head_hash"] is None
    assert heads == [store.status(NOW)["head_hash"], other.status(NOW)["head_hash"]]


def test_missing_and_nonshared_configuration_never_fabricates_aggregate_rows(tmp_path):
    from test_provider_spend_api import sample as standalone

    _, legacy = standalone(tmp_path)
    for app, state in [
        (create_app(operator_token="fixture-token", clock=lambda: NOW), "NOT_CONFIGURED"),
        (legacy, "NOT_SHARED"),
    ]:
        with TestClient(app) as client:
            value = client.get(PATH, headers=HEADERS).json()
            assert value["state"] == state and value["agents"] == value["models"] == []
            SharedSpendObservation.model_validate(value)


def test_corrupt_original_book_withholds_all_models_and_counters(tmp_path):
    store, _, _, _, app = sample(tmp_path)
    store.pin.write_bytes(b"corrupt")
    with TestClient(app) as client:
        response = client.get(PATH, headers=HEADERS)
        value = response.json()
        assert value["state"] == "UNAVAILABLE" and value["models"] == []
        assert value["agents"] == [] and value["head_hash"] is None
        assert str(tmp_path) not in response.text


def test_one_configured_route_without_a_book_withholds_shared_capacity(tmp_path):
    store, _, _, classifier, app = sample(tmp_path)
    classifier._spend_store = None
    before = store.status(NOW)["head_hash"]
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
        assert value["state"] == "INCOMPLETE_CONFIGURATION"
        assert value["agents"] == value["models"] == value["routes"] == []
        assert value["head_hash"] is None
        SharedSpendObservation.model_validate(value)
    assert store.status(NOW)["head_hash"] == before


def test_changed_active_provider_binding_during_observation_withholds_original_snapshot(tmp_path):
    store, _, _, classifier, app = sample(tmp_path)
    replacement = ProviderSpendStore(tmp_path / "replacement.db", policy())
    capture = store.shared_observation

    def changed(now):
        result = capture(now)
        classifier._spend_store = replacement
        return result

    store.shared_observation = changed
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
        assert value["state"] == "UNAVAILABLE" and value["agents"] == []


def test_breach_retains_exact_original_model_report_and_aggregate_sum(tmp_path):
    store, _, _, _, app = sample(tmp_path)
    key = reserve(store, now=NOW)
    with pytest.raises(PermissionError):
        store.settle(key, input_tokens=10**9, output_tokens=10**9, now=NOW)
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
        assert value["state"] == "BREACHED"
        assert value["agents"][1]["daily_tokens"] == 2000065656
        assert value["agents"][1]["daily_nano_usd"] == "10002752872"
        SharedSpendObservation.model_validate(value)


def test_native_frontend_fixture_matches_actual_book_except_random_original_identity(tmp_path):
    _, _, _, _, app = sample(tmp_path)
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/shared-spending.json"
        ).read_text(encoding="utf-8")
    )
    for key in ("book_id", "head_hash"):
        fixture[key] = value[key]
    assert fixture == value


def test_operator_session_revoked_during_shared_read_withholds_every_counter(tmp_path):
    store, _, _, _, app = sample(tmp_path)
    with TestClient(app) as client:
        grant = client.post("/api/v1/operator/sessions", headers=HEADERS)
        assert grant.status_code == 201
        headers = {"X-Operator-Session": grant.json()["session_token"]}
        capture = store.shared_observation

        def revoked(now):
            value = capture(now)
            assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
            return value

        store.shared_observation = revoked
        response = client.get(PATH, headers=headers)
        assert response.status_code == 401 and "agents" not in response.json()


@pytest.mark.parametrize("change", ["aggregate", "model", "duplicate_route", "precision"])
def test_shared_wire_validation_rejects_false_sums_and_model_metadata(tmp_path, change):
    _, _, _, _, app = sample(tmp_path)
    with TestClient(app) as client:
        value = client.get(PATH, headers=HEADERS).json()
    if change == "aggregate":
        row = value["models"][0]["agents"][1]
        for name in ("daily_tokens", "monthly_tokens"):
            row[name] += 1
        for name in ("daily_token_remaining", "monthly_token_remaining"):
            row[name] -= 1
    elif change == "model":
        value["models"][1]["accounting"] = "PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"
    elif change == "duplicate_route":
        value["routes"][1] = value["routes"][0]
    else:
        value["agents"][1]["daily_nano_usd"] = 2752872
    with pytest.raises(ValueError):
        SharedSpendObservation.model_validate(value)


def test_all_eight_native_model_groups_fit_the_bounded_private_read(tmp_path):
    from dataclasses import replace
    from test_shared_provider_spend import policy as shared_policy

    declared = shared_policy()
    original = next(row for row in declared.models if row.model == OPENAI_MODEL)
    extras = tuple(
        replace(original, model=f"gpt-fixture-{year}-01-01") for year in range(2020, 2026)
    )
    store = ProviderSpendStore(
        tmp_path / "eight.db", replace(declared, models=(*declared.models, *extras))
    )
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=OPENAI_MODEL,
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
    )
    app = create_app(
        operator_token="fixture-token",
        clock=lambda: NOW,
        deep_reviewer=reviewer,
        review_owner=ProviderOwner("A02", "0.2.0"),
    )
    with TestClient(app) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200 and response.json()["state"] == "ENFORCED"
        assert len(response.json()["models"]) == 8 and len(response.content) <= 262144
        SharedSpendObservation.model_validate(response.json())
