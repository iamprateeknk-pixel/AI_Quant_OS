"""Currency-twin source for A13: optional evidence, bounded pair signal."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ai_quant_os.currency_twin import REQUIRED_COMPONENTS, build_twin
from ai_quant_os.meta_allocator_sources import (
    INPUT_NAMES,
    AllocationInputs,
    TwinPairObservation,
    allocator_sources,
    allocator_sources_with_twin,
    twin_pair_signal,
)
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskLimits

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def make_inputs():
    return AllocationInputs(
        portfolio=PortfolioExposure.empty(),
        risk_limits=RiskLimits(Decimal(10), Decimal(20), 5),
        safety_state="NORMAL",
    )


def make_twin(currency, *, value="0.5", confidence="0.8", freshness=60):
    raw = {
        name: {
            "value": value,
            "confidence": confidence,
            "freshness_seconds": freshness,
            "source": "test-feed",
        }
        for name in REQUIRED_COMPONENTS
    }
    return build_twin(currency, raw)


def fetch_record(binding):
    return asyncio.run(binding.fetch())


def twin_payload(sources):
    record = fetch_record(sources["currency_twin"])
    return TwinPairObservation.model_validate(json.loads(record.payload_json))


def test_twin_absent_returns_allocator_sources_unchanged():
    inputs = make_inputs()
    baseline = allocator_sources(inputs, now=NOW)
    with_twin = allocator_sources_with_twin(inputs, now=NOW)
    assert list(with_twin.keys()) == list(baseline.keys())
    assert list(with_twin.keys()) == list(INPUT_NAMES)
    for name in baseline:
        assert with_twin[name].tool == baseline[name].tool
        assert with_twin[name].resource_class == baseline[name].resource_class
        base_record = fetch_record(baseline[name])
        twin_record = fetch_record(with_twin[name])
        assert twin_record.source_id == base_record.source_id
        assert twin_record.payload_json == base_record.payload_json


def test_twin_present_adds_currency_twin_source():
    inputs = make_inputs()
    baseline = allocator_sources(inputs, now=NOW)
    twin_a = make_twin("EUR", value="0.5", confidence="0.8")
    twin_b = make_twin("USD", value="0.1", confidence="0.8")
    sources = allocator_sources_with_twin(inputs, now=NOW, twin_a=twin_a, twin_b=twin_b)
    assert list(sources.keys()) == [*INPUT_NAMES, "currency_twin"]
    for name in baseline:
        assert sources[name].tool == baseline[name].tool
        assert sources[name].resource_class == baseline[name].resource_class
        base_record = fetch_record(baseline[name])
        record = fetch_record(sources[name])
        assert record.source_id == base_record.source_id
        assert record.payload_json == base_record.payload_json
    binding = sources["currency_twin"]
    assert binding.tool == "WORLD_STATE_READ"
    assert binding.resource_class == "GENERAL"
    parsed = twin_payload(sources)
    assert parsed.pair_score == twin_pair_signal(twin_a, twin_b)
    assert parsed.instrument_id == "EURUSD"
    assert parsed.base_currency == "EUR"
    assert parsed.quote_currency == "USD"
    expected = min(twin_a.overall_confidence(), twin_b.overall_confidence())
    assert parsed.combined_confidence == expected


def test_bullish_base_vs_bearish_quote_signal_is_positive():
    twin_a = make_twin("EUR", value="0.8", confidence="0.9")
    twin_b = make_twin("USD", value="-0.8", confidence="0.9")
    assert twin_pair_signal(twin_a, twin_b) > Decimal(0)


def test_low_confidence_both_legs_pair_score_is_exactly_zero():
    twin_a = make_twin("EUR", value="0.8", confidence="0.1")
    twin_b = make_twin("USD", value="-0.8", confidence="0.1")
    assert twin_pair_signal(twin_a, twin_b) == Decimal(0)


def test_twin_pair_signal_clamps_to_one():
    twin_a = make_twin("EUR", value="0.5", confidence="0.9")
    twin_b = make_twin("USD", value="0", confidence="0.9")
    signal = twin_pair_signal(twin_a, twin_b, microstructure=Decimal("5"))
    assert signal == Decimal(1)


def test_one_leg_none_base_missing_raises():
    inputs = make_inputs()
    twin_b = make_twin("USD", value="0.1", confidence="0.8")
    with pytest.raises(ValueError):
        allocator_sources_with_twin(inputs, now=NOW, twin_a=None, twin_b=twin_b)


def test_one_leg_none_quote_missing_raises():
    inputs = make_inputs()
    twin_a = make_twin("EUR", value="0.5", confidence="0.8")
    with pytest.raises(ValueError):
        allocator_sources_with_twin(inputs, now=NOW, twin_a=twin_a, twin_b=None)


def test_currency_mismatch_vs_instrument_raises():
    inputs = make_inputs()
    twin_a = make_twin("EUR", value="0.5", confidence="0.8")
    twin_b = make_twin("GBP", value="0.1", confidence="0.8")
    with pytest.raises(ValueError):
        allocator_sources_with_twin(inputs, now=NOW, twin_a=twin_a, twin_b=twin_b)


def test_staleness_halves_combined_confidence_in_payload():
    inputs = make_inputs()
    fresh_a = make_twin("EUR", value="0.5", confidence="0.8", freshness=60)
    fresh_b = make_twin("USD", value="0.2", confidence="0.8", freshness=60)
    fresh_sources = allocator_sources_with_twin(inputs, now=NOW, twin_a=fresh_a, twin_b=fresh_b)
    assert twin_payload(fresh_sources).combined_confidence == Decimal("0.8")
    stale_a = make_twin("EUR", value="0.5", confidence="0.8", freshness=7200)
    stale_b = make_twin("USD", value="0.2", confidence="0.8", freshness=7200)
    stale_sources = allocator_sources_with_twin(inputs, now=NOW, twin_a=stale_a, twin_b=stale_b)
    assert twin_payload(stale_sources).combined_confidence == Decimal("0.4")
