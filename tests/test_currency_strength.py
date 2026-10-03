"""Currency-return decomposition must not count redundant FX edges as new evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import importlib.util

import pytest

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def implementation():
    assert importlib.util.find_spec("ai_quant_os.currency_strength") is not None
    from ai_quant_os.currency_strength import PairHistory, currency_strength

    return PairHistory, currency_strength


def pair(identifier, base, quote, closes, *, offset=0):
    record, _ = implementation()
    return record(
        identifier,
        base,
        quote,
        tuple(NOW + timedelta(seconds=i + offset) for i in range(len(closes))),
        tuple(Decimal(str(value)) for value in closes),
        "fixture:" + identifier,
    )


def scores(result):
    return {row.currency: row.score_bps for row in result.estimates}


def test_triangle_has_only_two_basis_edges_and_no_additional_score_weight():
    _, calculate = implementation()
    eur = pair("EURUSD", "EUR", "USD", [1, "1.01", "1.02"])
    yen = pair("USDJPY", "USD", "JPY", [100, 101, 102])
    cross = pair("EURJPY", "EUR", "JPY", [100, "102.01", "104.04"])
    original = calculate(("EUR", "JPY", "USD"), (eur, yen))
    redundant = calculate(("EUR", "JPY", "USD"), (eur, yen, cross))
    assert original.status == redundant.status == "AVAILABLE"
    assert len(redundant.basis_instruments) == 2
    assert len(redundant.dependent_instruments) == 1
    for currency, score in scores(original).items():
        assert abs(score - scores(redundant)[currency]) < Decimal("1e-20")
    assert abs(sum(scores(redundant).values())) < Decimal("1e-20")
    for row in redundant.estimates:
        assert abs(
            sum(item.contribution_bps for item in row.decomposition) - row.score_bps
        ) < Decimal("1e-20")


def test_inverse_duplicate_is_checked_without_increasing_basis_rank():
    _, calculate = implementation()
    with localcontext() as context:
        context.prec = 50
        inverse = pair(
            "USDEUR", "USD", "EUR", [1, Decimal(1) / Decimal("1.01"), Decimal(1) / Decimal("1.02")]
        )
    eur = pair("EURUSD", "EUR", "USD", [1, "1.01", "1.02"])
    result = calculate(("USD", "EUR"), (inverse, eur))
    assert len(result.basis_instruments) == len(result.dependent_instruments) == 1
    assert result.status == "AVAILABLE"
    assert scores(result)["EUR"] > 0 > scores(result)["USD"]
    assert calculate(("EUR", "USD"), (eur, inverse)) == result


def test_inconsistent_middle_observation_with_equal_endpoint_withholds_component():
    _, calculate = implementation()
    rows = (
        pair("EURUSD", "EUR", "USD", [1, 1, 1]),
        pair("USDJPY", "USD", "JPY", [100, 100, 100]),
        pair("EURJPY", "EUR", "JPY", [100, 101, 100]),
    )
    result = calculate(("EUR", "USD", "JPY"), rows)
    assert result.status == "INCONSISTENT"
    assert all(row.status == "WITHHELD" and row.score_bps is None for row in result.estimates)
    assert any(row.max_residual_bps > 1 for row in result.residuals)


def test_disconnected_currencies_never_receive_a_global_rank_or_invented_score():
    _, calculate = implementation()
    result = calculate(("EUR", "USD", "JPY"), (pair("EURUSD", "EUR", "USD", [1, 1, "1.02"]),))
    assert result.status == "PARTIAL"
    assert scores(result)["JPY"] is None
    assert next(row for row in result.estimates if row.currency == "JPY").status == "UNAVAILABLE"
    assert scores(result)["EUR"] > 0 > scores(result)["USD"]


def test_asynchronous_return_windows_withhold_comparison():
    _, calculate = implementation()
    result = calculate(
        ("EUR", "USD", "JPY"),
        (
            pair("EURUSD", "EUR", "USD", [1, 1, "1.02"]),
            pair("USDJPY", "USD", "JPY", [100, 101, 102], offset=1),
        ),
    )
    assert result.status == "UNALIGNED"
    assert all(row.score_bps is None for row in result.estimates)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "1e100"])
def test_invalid_prices_refuse_before_logarithm(value):
    implementation()
    with pytest.raises(ValueError):
        pair("EURUSD", "EUR", "USD", [1, value, 1])


def test_duplicate_identity_and_unknown_currency_are_rejected():
    _, calculate = implementation()
    eur = pair("EURUSD", "EUR", "USD", [1, 1, 1])
    with pytest.raises(ValueError):
        calculate(("EUR", "USD"), (eur, eur))
    with pytest.raises(ValueError):
        calculate(("EUR", "JPY"), (eur,))


def test_frozen_history_refuses_mutable_sequences():
    record, _ = implementation()
    with pytest.raises(ValueError):
        record(
            "EURUSD",
            "EUR",
            "USD",
            tuple(NOW + timedelta(seconds=i) for i in range(3)),
            [Decimal(1)] * 3,
            "fixture:mutable",
        )
