"""Currency Digital Twins (blueprint section 17): validation and scoring."""

from decimal import Decimal

import pytest

from ai_quant_os.currency_twin import (
    REQUIRED_COMPONENTS,
    CurrencyTwin,
    TwinComponent,
    build_twin,
    pair_score,
)

ZERO = Decimal(0)


def component(name, *, value="0", confidence="0.8", freshness=60, source="test"):
    return TwinComponent(
        name=name,
        value=Decimal(value),
        confidence=Decimal(confidence),
        freshness_seconds=freshness,
        source=source,
    )


def make_twin(currency="EUR", *, value="0.5", confidence="0.8", freshness=60, **overrides):
    components = []
    for name in REQUIRED_COMPONENTS:
        fields = {"value": value, "confidence": confidence, "freshness": freshness}
        fields.update(overrides.get(name, {}))
        components.append(component(name, **fields))
    return CurrencyTwin(currency=currency, components=tuple(components))


def raw_twin_dict(**fields):
    entry = {
        "value": "0.5",
        "confidence": "0.8",
        "freshness_seconds": 60,
        "source": "fixture-feed",
    }
    entry.update(fields)
    return {name: dict(entry) for name in REQUIRED_COMPONENTS}


# --- TwinComponent validation -------------------------------------------------


@pytest.mark.parametrize("value", ["1.01", "-1.5", "2"])
def test_component_value_out_of_range_rejected(value):
    with pytest.raises(ValueError):
        component("policy", value=value)


@pytest.mark.parametrize("value", ["-1", "0", "1"])
def test_component_value_boundaries_accepted(value):
    assert component("policy", value=value).value == Decimal(value)


@pytest.mark.parametrize("confidence", ["-0.1", "1.5"])
def test_component_confidence_out_of_range_rejected(confidence):
    with pytest.raises(ValueError):
        component("policy", confidence=confidence)


def test_component_non_finite_numbers_rejected():
    with pytest.raises(ValueError):
        component("policy", value="NaN")
    with pytest.raises(ValueError):
        component("policy", confidence="Infinity")


def test_component_negative_freshness_rejected():
    with pytest.raises(ValueError):
        component("policy", freshness=-1)


def test_component_empty_source_rejected():
    with pytest.raises(ValueError):
        component("policy", source="  ")


# --- CurrencyTwin coverage -----------------------------------------------------


@pytest.mark.parametrize("currency", ["eur", "EU", "EURO", "E1R"])
def test_twin_currency_code_validated(currency):
    with pytest.raises(ValueError):
        make_twin(currency)


def test_twin_missing_component_rejected():
    twin_components = tuple(item for item in make_twin().components if item.name != "narrative")
    with pytest.raises(ValueError, match="missing"):
        CurrencyTwin(currency="EUR", components=twin_components)


def test_twin_duplicate_component_rejected():
    components = list(make_twin().components)
    components.append(components[0])
    with pytest.raises(ValueError, match="repeat"):
        CurrencyTwin(currency="EUR", components=tuple(components))


def test_twin_unknown_component_rejected():
    components = [
        component("sentiment") if item.name == "narrative" else item
        for item in make_twin().components
    ]
    with pytest.raises(ValueError, match="Unknown"):
        CurrencyTwin(currency="EUR", components=tuple(components))


def test_component_lookup():
    twin = make_twin(policy={"value": "0.25"})
    assert twin.component("policy").value == Decimal("0.25")
    with pytest.raises(KeyError):
        twin.component("does-not-exist")


# --- weighted_score -------------------------------------------------------------


def test_weighted_score_uniform_components():
    assert make_twin(value="0.5", confidence="0.8").weighted_score() == Decimal("0.5")


def test_weighted_score_is_confidence_weighted():
    twin = make_twin(
        value="-1",
        confidence="0.1",
        policy={"value": "1", "confidence": "0.9"},
    )
    # (0.9 * 1 + 7 * 0.1 * -1) / (0.9 + 7 * 0.1) = 0.2 / 1.6 = 0.125
    assert twin.weighted_score() == Decimal("0.125")


def test_weighted_score_zero_confidence_is_zero():
    assert make_twin(value="1", confidence="0").weighted_score() == ZERO


# --- overall_confidence and the staleness penalty --------------------------------


def test_overall_confidence_fresh_components():
    assert make_twin(confidence="0.8", freshness=60).overall_confidence() == Decimal("0.8")


def test_overall_confidence_all_stale_halves():
    twin = make_twin(confidence="0.8", freshness=7200)
    assert twin.overall_confidence() == Decimal("0.4")


def test_overall_confidence_one_stale_component():
    twin = make_twin(confidence="0.8", freshness=60, policy={"freshness": 7200})
    # (7 * 0.8 + 0.8 / 2) / 8 = 6.0 / 8 = 0.75
    assert twin.overall_confidence() == Decimal("0.75")


def test_staleness_boundary_is_strictly_over_one_hour():
    fresh = make_twin(confidence="1", freshness=3600)
    stale = make_twin(confidence="1", freshness=3601)
    assert fresh.overall_confidence() == Decimal(1)
    assert stale.overall_confidence() == Decimal("0.5")


# --- pair_score -------------------------------------------------------------------


def test_pair_score_favours_stronger_twin():
    strong = make_twin("EUR", value="0.6", confidence="0.9")
    weak = make_twin("USD", value="-0.2", confidence="0.9")
    score = pair_score(
        strong, weak, microstructure=ZERO, regime_state=ZERO, event_state=ZERO, costs=ZERO
    )
    # (0.6 - -0.2) * min(0.9, 0.9) = 0.72
    assert score == Decimal("0.72")
    assert score > ZERO


def test_pair_score_reversed_is_negative():
    strong = make_twin("EUR", value="0.6", confidence="0.9")
    weak = make_twin("USD", value="-0.2", confidence="0.9")
    score = pair_score(
        weak, strong, microstructure=ZERO, regime_state=ZERO, event_state=ZERO, costs=ZERO
    )
    assert score == Decimal("-0.72")


def test_pair_score_adjustments():
    twin = make_twin("EUR", value="0.4", confidence="0.9")
    other = make_twin("USD", value="0.4", confidence="0.9")
    score = pair_score(
        twin,
        other,
        microstructure=Decimal("0.01"),
        regime_state=Decimal("0.02"),
        event_state=Decimal("0.005"),
        costs=Decimal("0.003"),
    )
    # Equal twins: 0 + 0.01 + 0.02 - 0.005 - 0.003 = 0.022
    assert score == Decimal("0.022")


def test_pair_score_costs_drag():
    strong = make_twin("EUR", value="0.6", confidence="0.9")
    weak = make_twin("USD", value="-0.2", confidence="0.9")
    free = pair_score(
        strong, weak, microstructure=ZERO, regime_state=ZERO, event_state=ZERO, costs=ZERO
    )
    costly = pair_score(
        strong,
        weak,
        microstructure=ZERO,
        regime_state=ZERO,
        event_state=ZERO,
        costs=Decimal("0.05"),
    )
    assert costly == free - Decimal("0.05")


def test_pair_score_low_confidence_returns_zero():
    confident = make_twin("EUR", value="1", confidence="0.9")
    shaky = make_twin("USD", value="-1", confidence="0.2")
    score = pair_score(
        confident,
        shaky,
        microstructure=Decimal("0.05"),
        regime_state=Decimal("0.05"),
        event_state=ZERO,
        costs=ZERO,
    )
    # The weaker leg (0.2) is below the 0.3 floor: cash is a position.
    assert score == ZERO


def test_pair_score_custom_floor_admits_moderate_confidence():
    strong = make_twin("EUR", value="1", confidence="0.25")
    weak = make_twin("USD", value="-1", confidence="0.25")
    score = pair_score(
        strong,
        weak,
        microstructure=ZERO,
        regime_state=ZERO,
        event_state=ZERO,
        costs=ZERO,
        confidence_floor=Decimal("0.2"),
    )
    # (1 - -1) * 0.25 = 0.5
    assert score == Decimal("0.5")


def test_pair_score_staleness_can_close_the_gate():
    stale = make_twin("EUR", value="1", confidence="0.5", freshness=7200)
    fresh = make_twin("USD", value="-1", confidence="0.9")
    # Stale twin's overall confidence halves to 0.25, below the 0.3 floor.
    score = pair_score(
        stale, fresh, microstructure=ZERO, regime_state=ZERO, event_state=ZERO, costs=ZERO
    )
    assert score == ZERO


def test_pair_score_rejects_negative_costs_and_bad_floor():
    twin = make_twin()
    with pytest.raises(ValueError):
        pair_score(
            twin,
            twin,
            microstructure=ZERO,
            regime_state=ZERO,
            event_state=ZERO,
            costs=Decimal("-0.01"),
        )
    with pytest.raises(ValueError):
        pair_score(
            twin,
            twin,
            microstructure=ZERO,
            regime_state=ZERO,
            event_state=ZERO,
            costs=ZERO,
            confidence_floor=Decimal("1.5"),
        )


# --- build_twin -------------------------------------------------------------------


def test_build_twin_from_dict():
    twin = build_twin("GBP", raw_twin_dict())
    assert twin.currency == "GBP"
    assert twin.component("rates").source == "fixture-feed"
    assert twin.weighted_score() == Decimal("0.5")
    assert twin.overall_confidence() == Decimal("0.8")


def test_build_twin_accepts_numeric_types():
    raw = raw_twin_dict()
    raw["policy"] = {
        "value": 0.25,
        "confidence": 1,
        "freshness_seconds": 5,
        "source": "typed-feed",
    }
    twin = build_twin("JPY", raw)
    assert twin.component("policy").value == Decimal("0.25")
    assert twin.component("policy").confidence == Decimal(1)


def test_build_twin_missing_component_raises():
    raw = raw_twin_dict()
    del raw["liquidity"]
    with pytest.raises(ValueError, match="missing"):
        build_twin("EUR", raw)


def test_build_twin_missing_field_raises():
    raw = raw_twin_dict()
    del raw["policy"]["source"]
    with pytest.raises(ValueError, match="missing field"):
        build_twin("EUR", raw)


def test_build_twin_invalid_number_raises():
    raw = raw_twin_dict()
    raw["macro"]["value"] = "not-a-number"
    with pytest.raises(ValueError):
        build_twin("EUR", raw)


def test_build_twin_out_of_range_value_raises():
    raw = raw_twin_dict()
    raw["macro"]["value"] = "1.5"
    with pytest.raises(ValueError):
        build_twin("EUR", raw)
