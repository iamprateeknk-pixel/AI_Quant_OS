"""Protected coherent cost observations; no credentials, invoice claims or writes."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.openai_reviewer import OpenAIReviewer
from ai_quant_os.provider_call_admission import ProviderOwner
from ai_quant_os.provider_spend_api import SpendObservation
from ai_quant_os.provider_spend_store import ProviderSpendStore
from test_provider_spend import policy, MODEL

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
HEADERS = {"X-Operator-Token": "fixture-token"}


def sample(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy())
    store.reserve(
        agent_id="A02",
        agent_version="0.2.0",
        model=MODEL,
        request_sha256="a" * 64,
        input_tokens=100,
        max_output_tokens=128,
        now=NOW,
    )
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Read must never contact a provider")
        ),
    )
    return store, create_app(
        operator_token="fixture-token",
        clock=lambda: NOW,
        deep_reviewer=reviewer,
        review_owner=ProviderOwner("A02", "0.2.0"),
    )


def test_protected_native_cost_observation_retains_exact_holds_and_limits(tmp_path):
    store, app = sample(tmp_path)
    head = store.status(NOW)["head_hash"]
    with TestClient(app) as client:
        assert client.get("/api/v1/providers/review-spending").status_code == 401
        response = client.get("/api/v1/providers/review-spending", headers=HEADERS)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        SpendObservation.model_validate(value)
        assert value["state"] == "ENFORCED" and len(value["agents"]) == 18
        row = next(row for row in value["agents"] if row["agent_id"] == "A02")
        assert row["unsettled_tokens"] == 228 and row["unsettled_nano_usd"] == "1224"
        assert row["daily_money_limit"] == "100000" and row["daily_nano_usd"] == "1224"
        assert row["daily_token_limit"] == 1000 and row["daily_token_remaining"] == 672
        assert (
            value["billable_cost_accounting"] is False
            and value["recovery_spending_enabled"] is False
        )
        assert "fixture-key" not in response.text and str(tmp_path) not in response.text
    assert store.status(NOW)["head_hash"] == head


def test_unconfigured_and_corrupt_costs_never_invent_rows_or_totals(tmp_path):
    with TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW)) as client:
        value = client.get("/api/v1/providers/review-spending", headers=HEADERS).json()
        assert (
            value["state"] == "NOT_CONFIGURED" and value["agents"] == [] and value["model"] is None
        )
        SpendObservation.model_validate(value)
    store, app = sample(tmp_path)
    store.pin.write_bytes(b"corrupt")
    with TestClient(app) as client:
        response = client.get("/api/v1/providers/review-spending", headers=HEADERS)
        assert response.status_code == 200
        value = response.json()
        assert value["state"] == "UNAVAILABLE" and value["agents"] == []
        assert value["head_hash"] is None and str(tmp_path) not in response.text


def test_retained_budget_breach_is_visible_with_verified_original_counters(tmp_path):
    store, app = sample(tmp_path)
    import sqlite3

    with sqlite3.connect(store.path) as db:
        reservation = json.loads(
            db.execute("SELECT body FROM spend_events WHERE event_key LIKE 'reserve:%'").fetchone()[
                0
            ]
        )["reservation_id"]
    with pytest.raises(PermissionError):
        store.settle(reservation, input_tokens=101, output_tokens=20, now=NOW)
    with TestClient(app) as client:
        value = client.get("/api/v1/providers/review-spending", headers=HEADERS).json()
        assert value["state"] == "BREACHED"
        SpendObservation.model_validate(value)
        row = next(row for row in value["agents"] if row["agent_id"] == "A02")
        assert row["daily_tokens"] == 121 and row["unsettled_tokens"] == 0


def test_frontend_cost_fixture_matches_actual_protected_backend_except_journal_hash(tmp_path):
    _, app = sample(tmp_path)
    with TestClient(app) as client:
        value = client.get("/api/v1/providers/review-spending", headers=HEADERS).json()
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/review-spending.json"
        ).read_text(encoding="utf-8")
    )
    fixture["head_hash"] = value["head_hash"]
    assert fixture == value


def test_native_breach_amount_above_javascript_integer_precision_is_an_exact_string(tmp_path):
    from dataclasses import replace

    declared = replace(policy(money=10**14), input_nano_usd=10**9, output_nano_usd=10**9)
    store = ProviderSpendStore(tmp_path / "large-spend.db", declared)
    key = store.reserve(
        agent_id="A02",
        agent_version="0.2.0",
        model=MODEL,
        request_sha256="a" * 64,
        input_tokens=100,
        max_output_tokens=128,
        now=NOW,
    )
    with pytest.raises(PermissionError):
        store.settle(key, input_tokens=10**9, output_tokens=10**9, now=NOW)
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
    )
    with TestClient(
        create_app(
            operator_token="fixture-token",
            clock=lambda: NOW,
            deep_reviewer=reviewer,
            review_owner=ProviderOwner("A02", "0.2.0"),
        )
    ) as client:
        value = client.get("/api/v1/providers/review-spending", headers=HEADERS).json()
        assert value["state"] == "BREACHED"
        row = next(row for row in value["agents"] if row["agent_id"] == "A02")
        assert row["daily_nano_usd"] == "2000000000000000000"
        assert row["daily_money_remaining"] == "0"


@pytest.mark.parametrize(
    "change",
    [
        {"daily_token_remaining": 673},
        {"daily_money_remaining": "98777"},
        {"pending_count": 0},
        {"daily_nano_usd": 1224},
    ],
)
def test_typed_observation_rejects_inconsistent_capacity_and_rounded_amounts(tmp_path, change):
    _, app = sample(tmp_path)
    with TestClient(app) as client:
        value = client.get("/api/v1/providers/review-spending", headers=HEADERS).json()
    value["agents"][1].update(change)
    with pytest.raises(ValueError):
        SpendObservation.model_validate(value)


def test_session_revoked_during_ledger_read_withholds_the_observation(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy())
    reviewer = OpenAIReviewer(
        api_key="fixture-key",
        model=MODEL,
        spend_store=store,
        spend_owner=ProviderOwner("A02", "0.2.0"),
        utc_clock=lambda: NOW,
    )
    app = create_app(
        operator_token="fixture-token",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.db",
        deep_reviewer=reviewer,
        review_owner=ProviderOwner("A02", "0.2.0"),
    )
    with TestClient(app) as client:
        grant = client.post("/api/v1/operator/sessions", headers=HEADERS)
        assert grant.status_code == 201
        headers = {"X-Operator-Session": grant.json()["session_token"]}
        original = reviewer.spending_observation

        def revoked_read(now):
            value = original(now)
            assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
            return value

        reviewer.spending_observation = revoked_read
        response = client.get("/api/v1/providers/review-spending", headers=headers)
        assert response.status_code == 401 and "agents" not in response.json()
