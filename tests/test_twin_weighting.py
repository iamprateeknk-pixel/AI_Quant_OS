"""Phase 3: currency-twin weighting driven by the versioned risk policy.

Covers (a) policy-absent output identical to Phase 2, (b) weight 0,
(c) weight > 0 within the clamp, (d) the confidence floor, (e) the
``twin_evidence_weight`` risk-policy field and its fail-closed loading.
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ai_quant_os.currency_twin import REQUIRED_COMPONENTS, build_twin
from ai_quant_os.meta_allocator_sources import (
    INPUT_NAMES,
    AllocationInputs,
    TwinPairObservation,
    WeightedTwinPairObservation,
    allocator_sources,
    allocator_sources_with_twin,
    apply_twin_weight,
    twin_pair_signal,
)
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskLimits
from ai_quant_os.risk_policy import DEFAULT_POLICY, PaperRiskPolicy, load_risk_policy

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]

PHASE2_PAYLOAD_KEYS = {
    "as_of",
    "availability",
    "instrument_id",
    "base_currency",
    "quote_currency",
    "pair_score",
    "combined_confidence",
    "coverage",
}


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


def strong_twins():
    twin_a = make_twin("EUR", value="0.5", confidence="0.8")
    twin_b = make_twin("USD", value="0.1", confidence="0.8")
    return twin_a, twin_b


def fetch_record(binding):
    return asyncio.run(binding.fetch())


def weighted_payload(sources):
    record = fetch_record(sources["currency_twin"])
    return WeightedTwinPairObservation.model_validate(json.loads(record.payload_json))


def example_payload():
    return json.loads((ROOT / "data" / "risk_policy.paper.v1.json").read_text())


def write_policy(tmp_path, payload):
    target = tmp_path / "policy.json"
    target.write_text(json.dumps(payload))
    return target


# (a) Policy absent -> output identical to Phase 2.


def test_policy_absent_matches_phase2_payload_and_source_id():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    baseline = allocator_sources_with_twin(inputs, now=NOW, twin_a=twin_a, twin_b=twin_b)
    explicit_none = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=None
    )
    assert list(explicit_none.keys()) == list(baseline.keys())
    for name in baseline:
        base_record = fetch_record(baseline[name])
        none_record = fetch_record(explicit_none[name])
        assert none_record.source_id == base_record.source_id
        assert none_record.payload_json == base_record.payload_json
    twin_record = fetch_record(explicit_none["currency_twin"])
    raw = json.loads(twin_record.payload_json)
    assert set(raw.keys()) == PHASE2_PAYLOAD_KEYS
    parsed = TwinPairObservation.model_validate(raw)
    assert parsed.pair_score == twin_pair_signal(twin_a, twin_b)


def test_policy_absent_without_twins_matches_allocator_sources():
    inputs = make_inputs()
    baseline = allocator_sources(inputs, now=NOW)
    with_none = allocator_sources_with_twin(inputs, now=NOW, risk_policy=None)
    assert list(with_none.keys()) == list(INPUT_NAMES) == list(baseline.keys())
    for name in baseline:
        assert fetch_record(with_none[name]).source_id == fetch_record(baseline[name]).source_id
        assert (
            fetch_record(with_none[name]).payload_json == fetch_record(baseline[name]).payload_json
        )


def test_policy_with_default_weight_keeps_base_sources_unchanged():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    baseline = allocator_sources_with_twin(inputs, now=NOW, twin_a=twin_a, twin_b=twin_b)
    weighted = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=DEFAULT_POLICY
    )
    assert list(weighted.keys()) == list(baseline.keys())
    for name in INPUT_NAMES:
        assert fetch_record(weighted[name]).source_id == fetch_record(baseline[name]).source_id
        assert (
            fetch_record(weighted[name]).payload_json == fetch_record(baseline[name]).payload_json
        )


def test_non_policy_risk_policy_argument_fails_closed():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    with pytest.raises(ValueError):
        allocator_sources_with_twin(
            inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=object()
        )


# (b) Weight 0 -> weighted exactly 0, pair_score unchanged.


def test_weight_zero_weighted_score_is_exactly_zero():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    policy = PaperRiskPolicy(twin_evidence_weight=Decimal("0"))
    sources = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=policy
    )
    parsed = weighted_payload(sources)
    assert parsed.twin_evidence_weight == Decimal("0")
    assert parsed.weighted_pair_score == Decimal("0")
    assert parsed.pair_score == twin_pair_signal(twin_a, twin_b)
    assert parsed.pair_score != Decimal("0")


def test_default_policy_weight_is_zero():
    assert DEFAULT_POLICY.twin_evidence_weight == Decimal("0")
    assert PaperRiskPolicy().twin_evidence_weight == Decimal("0")


# (c) Weight > 0 -> weighted shifts within the clamp, never beyond it.


@pytest.mark.parametrize(
    ("pair_score", "weight", "expected"),
    [
        (Decimal("0.8"), Decimal("0.5"), Decimal("0.4")),
        (Decimal("-0.8"), Decimal("0.5"), Decimal("-0.4")),
        (Decimal("1"), Decimal("0.3"), Decimal("0.3")),
        (Decimal("-1"), Decimal("0.3"), Decimal("-0.3")),
        (Decimal("0.5"), Decimal("1"), Decimal("0.5")),
        (Decimal("0.25"), Decimal("0.5"), Decimal("0.125")),
        (Decimal("0"), Decimal("0.9"), Decimal("0")),
        (Decimal("0.9"), Decimal("0"), Decimal("0")),
    ],
)
def test_apply_twin_weight_values(pair_score, weight, expected):
    assert apply_twin_weight(pair_score, weight) == expected


@pytest.mark.parametrize(
    ("pair_score", "weight"),
    [
        (Decimal("1"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("1"), Decimal("0.01")),
        (Decimal("-0.001"), Decimal("0.99")),
        (Decimal("0.37"), Decimal("0.62")),
        (Decimal("-0.62"), Decimal("0.37")),
        (Decimal("0"), Decimal("1")),
        (Decimal("0.5"), Decimal("0")),
    ],
)
def test_apply_twin_weight_never_widens_signal(pair_score, weight):
    weighted = apply_twin_weight(pair_score, weight)
    assert abs(weighted) <= min(abs(pair_score), weight)
    assert abs(weighted) <= weight
    if pair_score > 0:
        assert Decimal("0") <= weighted <= pair_score
    elif pair_score < 0:
        assert pair_score <= weighted <= Decimal("0")


@pytest.mark.parametrize("weight", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")])
def test_apply_twin_weight_rejects_invalid_weights(weight):
    with pytest.raises(ValueError):
        apply_twin_weight(Decimal("0.5"), weight)


def test_weighted_payload_scales_pair_score():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    policy = PaperRiskPolicy(twin_evidence_weight=Decimal("0.5"))
    sources = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=policy
    )
    parsed = weighted_payload(sources)
    signal = twin_pair_signal(twin_a, twin_b)
    assert parsed.pair_score == signal
    assert parsed.twin_evidence_weight == Decimal("0.5")
    assert parsed.weighted_pair_score == apply_twin_weight(signal, Decimal("0.5"))
    assert Decimal("0") < parsed.weighted_pair_score < signal


def test_weighted_payload_negative_signal_stays_negative_and_bounded():
    inputs = make_inputs()
    twin_a = make_twin("EUR", value="-0.8", confidence="0.9")
    twin_b = make_twin("USD", value="0.8", confidence="0.9")
    policy = PaperRiskPolicy(twin_evidence_weight=Decimal("0.25"))
    sources = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=policy
    )
    parsed = weighted_payload(sources)
    assert parsed.pair_score < Decimal("0")
    assert parsed.weighted_pair_score < Decimal("0")
    assert abs(parsed.weighted_pair_score) <= min(abs(parsed.pair_score), Decimal("0.25"))


def test_weighted_payload_clamps_raw_signal_above_one():
    inputs = make_inputs()
    twin_a, twin_b = strong_twins()
    policy = PaperRiskPolicy(twin_evidence_weight=Decimal("0.4"))
    sources = allocator_sources_with_twin(
        inputs,
        now=NOW,
        twin_a=twin_a,
        twin_b=twin_b,
        microstructure=Decimal("5"),
        risk_policy=policy,
    )
    parsed = weighted_payload(sources)
    assert parsed.pair_score == Decimal("1")
    assert parsed.weighted_pair_score == Decimal("0.4")


# (d) Confidence floor -> pair_score 0 -> weighted 0 even with weight 1.


def test_confidence_floor_zeroes_weighted_score_at_weight_one():
    inputs = make_inputs()
    twin_a = make_twin("EUR", value="0.8", confidence="0.1")
    twin_b = make_twin("USD", value="-0.8", confidence="0.1")
    policy = PaperRiskPolicy(twin_evidence_weight=Decimal("1"))
    sources = allocator_sources_with_twin(
        inputs, now=NOW, twin_a=twin_a, twin_b=twin_b, risk_policy=policy
    )
    parsed = weighted_payload(sources)
    assert parsed.pair_score == Decimal("0")
    assert parsed.weighted_pair_score == Decimal("0")


# (e) Risk policy field: default, round-trip, fail-closed, digest.


def test_policy_file_without_weight_matches_default_digest(tmp_path):
    loaded = load_risk_policy(write_policy(tmp_path, example_payload()))
    assert loaded == DEFAULT_POLICY
    assert loaded.twin_evidence_weight == Decimal("0")
    assert loaded.digest == DEFAULT_POLICY.digest


def test_policy_file_with_weight_round_trips(tmp_path):
    payload = example_payload()
    payload["twin_evidence_weight"] = "0.25"
    loaded = load_risk_policy(write_policy(tmp_path, payload))
    assert loaded.twin_evidence_weight == Decimal("0.25")
    assert loaded.digest != DEFAULT_POLICY.digest


def test_shipped_example_file_still_loads_as_default():
    loaded = load_risk_policy(ROOT / "data" / "risk_policy.paper.v1.json")
    assert loaded == DEFAULT_POLICY
    assert loaded.digest == DEFAULT_POLICY.digest


@pytest.mark.parametrize("bad", ["-0.1", "1.1", "NaN", "abc"])
def test_invalid_weight_in_file_fails_closed(tmp_path, bad):
    payload = example_payload()
    payload["twin_evidence_weight"] = bad
    with pytest.raises(ValueError):
        load_risk_policy(write_policy(tmp_path, payload))


@pytest.mark.parametrize("bad", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")])
def test_invalid_weight_construction_fails_closed(bad):
    with pytest.raises(ValueError):
        PaperRiskPolicy(twin_evidence_weight=bad)


def test_unknown_field_still_fails_closed_with_optional_field_present(tmp_path):
    payload = example_payload()
    payload["twin_evidence_weight"] = "0.5"
    payload["unknown_field"] = "x"
    with pytest.raises(ValueError):
        load_risk_policy(write_policy(tmp_path, payload))
