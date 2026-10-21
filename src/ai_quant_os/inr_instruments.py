"""INR-first paper instruments (V3.2 Master Blueprint, section 37).

Section 37 sets the initial market scope as India exchange-traded FX first:
USDINR, EURINR, GBPINR and JPYINR, futures first. This module declares that
catalogue. It deliberately does **not** extend
``ai_quant_os.paper_instruments.PAPER_INSTRUMENTS`` — see
``INR_INSTRUMENT_STATUS`` below for why the merge is blocked.
"""

from ai_quant_os.currency_portfolio import CurrencyInstrument

# The declared INR-first catalogue. Note the NSE-style convention for the
# yen pair: the instrument id is "JPYINR" while its contract size is quoted
# per 100 JPY; the currency legs here are JPY/INR, matching the other pairs.
INR_PAPER_INSTRUMENTS: tuple[CurrencyInstrument, ...] = (
    CurrencyInstrument("USDINR", "USD", "INR"),
    CurrencyInstrument("EURINR", "EUR", "INR"),
    CurrencyInstrument("GBPINR", "GBP", "INR"),
    CurrencyInstrument("JPYINR", "JPY", "INR"),
)

#: Readiness of the INR catalogue. The instruments are *declared* but the
#: catalogue is NOT yet merged into ``PAPER_INSTRUMENTS``, so nothing in the
#: running paper system can trade or value them yet. The merge is blocked
#: because:
#:
#: (a) USD reporting needs an INR reference rate. The currency-risk code
#:     converts every position into the USD reporting currency, and for the
#:     cross pairs (EURINR, GBPINR, JPYINR) that conversion runs through
#:     the INR leg into USDINR. No INR reference-rate feed is wired into
#:     the data fabric yet, so INR positions cannot be valued or risk-
#:     checked in USD terms. See :func:`conversion_path`.
#: (b) The fixed paper schemas, API contracts and tests assert the current
#:     four-instrument catalogue (EURUSD, GBPUSD, USDJPY, EURJPY). Merging
#:     is a breaking contract change that must land together with the data
#:     feed and the reporting conversion, not as an isolated edit.
#:
#: Flipping this constant before (a) and (b) are resolved would advertise
#: instruments the platform can neither price nor risk-manage.
INR_INSTRUMENT_STATUS = "PENDING_DATA"

# How each INR instrument is valued in the USD reporting currency, as the
# ordered tuple of instrument ids whose rates are needed. USDINR values
# directly. Every cross pair converts through its INR leg into USDINR
# (e.g. EURINR -> INR via EURINR, then INR -> USD via USDINR).
_CONVERSION_PATHS: dict[str, tuple[str, ...]] = {
    "USDINR": ("USDINR",),
    "EURINR": ("EURINR", "USDINR"),
    "GBPINR": ("GBPINR", "USDINR"),
    "JPYINR": ("JPYINR", "USDINR"),
}


def conversion_path(instrument: CurrencyInstrument) -> tuple[str, ...]:
    """Return the instrument ids needed to value ``instrument`` in USD.

    The paper system's reporting currency is USD. USDINR converts directly,
    so its path is ``("USDINR",)``. The cross pairs carry an INR quote leg,
    so valuation runs through the INR leg into USDINR: EURINR, for example,
    returns ``("EURINR", "USDINR")`` — value the EUR/INR rate first, then
    convert INR into USD through USDINR.

    Raises ``ValueError`` for an instrument outside the declared INR
    catalogue: an unknown pair must fail loudly instead of being valued
    through a guessed path.
    """
    try:
        return _CONVERSION_PATHS[instrument.instrument_id]
    except KeyError:
        raise ValueError(
            f"No USD conversion path declared for {instrument.instrument_id}"
        ) from None


def validate_inr_catalog() -> bool:
    """Check the declared INR catalogue is internally consistent.

    Returns ``True`` only when every instrument id is unique, INR is one
    leg (base or quote) of every pair, every currency code is a three-letter
    uppercase code, no instrument pairs a currency with itself, and a USD
    conversion path is declared for every instrument. Returns ``False``
    rather than raising: this is a readiness probe callers can poll.
    """
    ids = [item.instrument_id for item in INR_PAPER_INSTRUMENTS]
    if len(set(ids)) != len(ids):
        return False
    for item in INR_PAPER_INSTRUMENTS:
        if "INR" not in (item.base_currency, item.quote_currency):
            return False
        if item.base_currency == item.quote_currency:
            return False
        for code in (item.base_currency, item.quote_currency):
            if len(code) != 3 or not code.isalpha() or code.upper() != code:
                return False
        if item.instrument_id not in _CONVERSION_PATHS:
            return False
    return True


__all__ = [
    "INR_INSTRUMENT_STATUS",
    "INR_PAPER_INSTRUMENTS",
    "conversion_path",
    "validate_inr_catalog",
]
