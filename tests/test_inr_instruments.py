"""INR-first instruments (blueprint section 37): declared, not yet merged."""

import pytest

from ai_quant_os.currency_portfolio import CurrencyInstrument
from ai_quant_os.inr_instruments import (
    INR_INSTRUMENT_STATUS,
    INR_PAPER_INSTRUMENTS,
    conversion_path,
    validate_inr_catalog,
)
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def by_id():
    return {item.instrument_id: item for item in INR_PAPER_INSTRUMENTS}


def test_catalog_contents():
    assert tuple(item.instrument_id for item in INR_PAPER_INSTRUMENTS) == (
        "USDINR",
        "EURINR",
        "GBPINR",
        "JPYINR",
    )
    legs = {
        item.instrument_id: (item.base_currency, item.quote_currency)
        for item in INR_PAPER_INSTRUMENTS
    }
    assert legs == {
        "USDINR": ("USD", "INR"),
        "EURINR": ("EUR", "INR"),
        "GBPINR": ("GBP", "INR"),
        "JPYINR": ("JPY", "INR"),
    }


def test_every_pair_has_inr_as_quote_leg():
    for item in INR_PAPER_INSTRUMENTS:
        assert item.quote_currency == "INR"
        assert item.base_currency != "INR"


def test_catalog_ids_unique():
    ids = [item.instrument_id for item in INR_PAPER_INSTRUMENTS]
    assert len(set(ids)) == len(ids)


def test_status_is_pending_data():
    assert INR_INSTRUMENT_STATUS == "PENDING_DATA"


def test_not_yet_merged_into_paper_instruments():
    paper_ids = {item.instrument_id for item in PAPER_INSTRUMENTS}
    assert paper_ids == {"EURUSD", "GBPUSD", "USDJPY", "EURJPY"}
    assert paper_ids.isdisjoint(by_id())


def test_validate_inr_catalog():
    assert validate_inr_catalog() is True


def test_conversion_path_usdinr_is_direct():
    assert conversion_path(by_id()["USDINR"]) == ("USDINR",)


@pytest.mark.parametrize(
    "instrument_id,expected",
    [
        ("EURINR", ("EURINR", "USDINR")),
        ("GBPINR", ("GBPINR", "USDINR")),
        ("JPYINR", ("JPYINR", "USDINR")),
    ],
)
def test_conversion_path_crosses_route_through_usdinr(instrument_id, expected):
    assert conversion_path(by_id()[instrument_id]) == expected


def test_conversion_paths_start_with_own_id_and_end_with_usdinr():
    for item in INR_PAPER_INSTRUMENTS:
        path = conversion_path(item)
        assert path[0] == item.instrument_id
        assert path[-1] == "USDINR"
        assert all(isinstance(step, str) for step in path)


def test_conversion_path_unknown_instrument_rejected():
    audusd = CurrencyInstrument("AUDUSD", "AUD", "USD")
    with pytest.raises(ValueError, match="AUDUSD"):
        conversion_path(audusd)


def test_conversion_path_undeclared_inr_pair_rejected():
    # An INR pair outside the declared catalogue must fail loudly, not be
    # valued through a guessed path.
    audinr = CurrencyInstrument("AUDINR", "AUD", "INR")
    with pytest.raises(ValueError, match="AUDINR"):
        conversion_path(audinr)
