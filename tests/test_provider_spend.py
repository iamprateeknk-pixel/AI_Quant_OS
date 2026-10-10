"""Durable reported-token admission, declared rate ceilings and uncertain holds."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.provider_spend_policy import ProviderSpendPolicy, SpendRule
from ai_quant_os.provider_spend_store import ProviderSpendStore

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
MODEL = "gpt-4o-2024-08-06"


def policy(tokens=1000, money=100000, reserve=100):
    return ProviderSpendPolicy(
        model=MODEL,
        input_nano_usd=2,
        output_nano_usd=8,
        rate_reference="fixture-declared-ceiling",
        rules=tuple(
            SpendRule(
                agent_id=agent,
                daily_tokens=tokens,
                monthly_tokens=tokens * 2,
                daily_token_reserve=reserve,
                monthly_token_reserve=reserve * 2,
                daily_nano_usd=money,
                monthly_nano_usd=money * 2,
                daily_money_reserve=100,
                monthly_money_reserve=200,
            )
            for agent in ROLE_CEILINGS
        ),
    )


def reserve(store, now=NOW, agent="A01", inputs=100, outputs=128, version="0.1.0"):
    return store.reserve(
        agent_id=agent,
        agent_version=version,
        model=MODEL,
        request_sha256="a" * 64,
        input_tokens=inputs,
        max_output_tokens=outputs,
        now=now,
    )


def agent(store, now=NOW):
    return next(row for row in store.status(now)["agents"] if row["agent_id"] == "A01")


def test_original_reservation_and_usage_survive_restart_and_release_only_unused_capacity(tmp_path):
    path = tmp_path / "spend.db"
    store = ProviderSpendStore(path, policy())
    reservation = reserve(store)
    assert agent(store)["daily_tokens"] == 228
    assert agent(store)["daily_nano_usd"] == 1224
    reopened = ProviderSpendStore(path, None)
    assert agent(reopened)["pending_count"] == 1
    reopened.settle(reservation, input_tokens=100, output_tokens=20, now=NOW)
    assert agent(reopened)["daily_tokens"] == 120
    assert agent(reopened)["daily_nano_usd"] == 360
    assert agent(reopened)["pending_count"] == 0
    reopened.settle(reservation, input_tokens=100, output_tokens=20, now=NOW)
    with pytest.raises(ValueError):
        reopened.settle(reservation, input_tokens=100, output_tokens=21, now=NOW)


def test_daily_monthly_ceiling_and_recovery_reserve_are_shared_across_versions(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy(tokens=300, reserve=50))
    first = reserve(store)
    store.settle(first, input_tokens=100, output_tokens=128, now=NOW)
    with pytest.raises(PermissionError):
        reserve(store, inputs=1, version="0.2.0")
    second = reserve(store, NOW + timedelta(days=1), version="0.2.0")
    store.settle(second, input_tokens=100, output_tokens=128, now=NOW + timedelta(days=1))
    with pytest.raises(PermissionError):
        reserve(store, NOW + timedelta(days=2))
    assert agent(store, NOW + timedelta(days=2))["monthly_tokens"] == 456
    assert agent(store, NOW + timedelta(days=2))["daily_tokens"] == 0


def test_unknown_usage_carries_full_hold_across_utc_periods_without_reset(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy(tokens=300, reserve=50))
    reserve(store)
    later = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert agent(store, later)["daily_tokens"] == 228
    assert agent(store, later)["monthly_tokens"] == 228
    with pytest.raises(PermissionError):
        reserve(store, later)


def test_declared_money_ceiling_can_exhaust_before_token_ceiling(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy(money=1000))
    with pytest.raises(PermissionError):
        reserve(store)
    assert agent(store)["pending_count"] == 0


def test_concurrent_sqlite_owners_cannot_oversubscribe(tmp_path):
    path = tmp_path / "spend.db"
    ProviderSpendStore(path, policy(tokens=300, reserve=50))

    def attempt(_):
        try:
            reserve(ProviderSpendStore(path, None))
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(attempt, range(8))) == 1


@pytest.mark.parametrize("damage", ["record", "pin", "missing"])
def test_changed_archive_and_original_missing_database_never_reset(tmp_path, damage):
    path = tmp_path / "spend.db"
    store = ProviderSpendStore(path, policy())
    reserve(store)
    if damage == "record":
        with sqlite3.connect(path) as db:
            db.execute("UPDATE spend_events SET body=? WHERE event_key LIKE 'reserve:%'", (b"{}",))
    elif damage == "pin":
        path.with_suffix(".db.pin.json").write_text("{}")
    else:
        path.unlink()
    with pytest.raises((ValueError, sqlite3.Error, FileNotFoundError)):
        reserve(store)
    with pytest.raises((ValueError, sqlite3.Error, FileNotFoundError)):
        ProviderSpendStore(path, policy())


def test_provider_report_over_bound_is_retained_and_latches_budget_breach(tmp_path):
    store = ProviderSpendStore(tmp_path / "spend.db", policy())
    reservation = reserve(store)
    with pytest.raises(PermissionError):
        store.settle(reservation, input_tokens=101, output_tokens=20, now=NOW)
    assert store.status(NOW)["state"] == "BREACHED"
    assert agent(store)["daily_tokens"] == 121
    with pytest.raises(PermissionError):
        reserve(store, agent="A02")


def test_immutable_policy_model_and_backwards_clock_are_rejected(tmp_path):
    path = tmp_path / "spend.db"
    store = ProviderSpendStore(path, policy())
    reserve(store)
    with pytest.raises(ValueError):
        ProviderSpendStore(path, policy(tokens=2000))
    with pytest.raises(ValueError):
        reserve(store, NOW - timedelta(seconds=1))
    with pytest.raises(ValueError):
        store.reserve(
            agent_id="A01",
            agent_version="0.2.0",
            model="gpt-4o",
            request_sha256="a" * 64,
            input_tokens=100,
            max_output_tokens=128,
            now=NOW,
        )


@pytest.mark.parametrize("value", [True, -1, 1.5, 10**12])
def test_policy_rejects_non_integer_or_unbounded_token_values(value):
    with pytest.raises(ValueError):
        policy(tokens=value)


def test_configuration_schema_and_runtime_preserve_pinned_policy_contract():
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/schemas/provider-spend-policy.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    value = json.loads(policy().encode())
    Draft202012Validator(schema).validate(value)
    assert ProviderSpendPolicy.decode(policy().encode()) == policy()
    duplicate = {**value, "rules": [value["rules"][0]] * 18}
    assert not Draft202012Validator(schema).is_valid(duplicate)
    with pytest.raises(ValueError):
        ProviderSpendPolicy.decode(json.dumps(duplicate).encode())
    with pytest.raises(ValueError):
        ProviderSpendPolicy.decode(
            policy()
            .encode()
            .replace(b'"schema_version":"1.0"', b'"schema_version":"1.0","schema_version":"1.0"')
        )
