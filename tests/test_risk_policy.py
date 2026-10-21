"""Versioned risk policy: defaults must reproduce the old api.py literals,
and configured files must fail closed on any defect."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.risk_policy import DEFAULT_POLICY, load_risk_policy

ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_matches_historical_literals():
    assert DEFAULT_POLICY.max_order_exposure == Decimal("1000")
    assert DEFAULT_POLICY.max_portfolio_exposure == Decimal("1500")
    assert DEFAULT_POLICY.max_quote_age_seconds == 5
    currency = DEFAULT_POLICY.currency_policy(PAPER_INSTRUMENTS)
    assert currency.reporting_currency == "USD"
    assert currency.max_currency_exposure_reporting == Decimal("2000")
    assert currency.max_gross_exposure_reporting == Decimal("4000")
    assert len(DEFAULT_POLICY.digest) == 64


def test_shipped_example_file_loads_and_matches_default(tmp_path):
    example = ROOT / "data" / "risk_policy.paper.v1.json"
    loaded = load_risk_policy(example)
    assert loaded == DEFAULT_POLICY
    assert loaded.digest == DEFAULT_POLICY.digest


def test_custom_policy_changes_limits(tmp_path):
    payload = json.loads((ROOT / "data" / "risk_policy.paper.v1.json").read_text())
    payload["policy_version"] = "paper-risk-1.1.0"
    payload["max_order_exposure"] = "250"
    target = tmp_path / "policy.json"
    target.write_text(json.dumps(payload))
    loaded = load_risk_policy(target)
    assert loaded.risk_limits().max_order_exposure == Decimal("250")
    assert loaded.digest != DEFAULT_POLICY.digest


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(unknown_field="x"),
        lambda p: p.pop("max_order_exposure"),
        lambda p: p.update(format_version="9.9"),
        lambda p: p.update(max_order_exposure="-5"),
        lambda p: p.update(reporting_currency="usd"),
    ],
)
def test_broken_policy_fails_closed(tmp_path, mutate):
    payload = json.loads((ROOT / "data" / "risk_policy.paper.v1.json").read_text())
    mutate(payload)
    target = tmp_path / "policy.json"
    target.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_risk_policy(target)
