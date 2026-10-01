"""Reconcile retained native bar evidence against its declared position/source model."""

from decimal import Context, Decimal, Inexact, ROUND_CEILING, ROUND_FLOOR, localcontext

from ai_quant_os.market_data import BarDataset
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def verify_native_bar_result(result, bars, quantity, costs):
    context = Context(prec=28)
    context.traps[Inexact] = True
    with localcontext(context):
        source = BarDataset(bars)
        instrument = next(
            (row for row in PAPER_INSTRUMENTS if row.instrument_id == result.instrument_id), None
        )
        if (
            not bars
            or source.bars != bars
            or source.duplicate_rows
            or instrument is None
            or result.pnl_currency != instrument.quote_currency
            or {bar.instrument_id for bar in bars} != {result.instrument_id}
            or result.dataset_fingerprint != source.fingerprint
            or result.costs != costs
            or result.engine != "NAUTILUS_BACKTEST"
            or result.engine_version != "1.221.0"
            or result.schema_version != "1.0"
            or result.venue != "PAPER"
            or result.replay_model
            not in ("BAR_OPEN_CLOSE_QUOTES_V1", "BAR_OPEN_CLOSE_LONG_SHORT_V1")
            or result.pnl_method != "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
        ):
            raise ValueError("Native bar source/model binding mismatch")
        directional = result.replay_model == "BAR_OPEN_CLOSE_LONG_SHORT_V1"
        position, cash = reconcile_bar_fills(
            result.fills, bars, quantity, costs, directional=directional
        )
        if (
            type(result.native_order_count) is not int
            or result.native_order_count != len(result.fills)
            or result.final_position != position
            or cash + position * bars[-1].close != result.net_pnl
        ):
            raise ValueError("Native cash-flow ledger mismatch")


def reconcile_bar_fills(fills, bars, quantity, costs, *, directional, fees_in_quote_currency=True):
    """Reconcile canonical bar fills; external-currency fees are valued by the caller."""
    context = Context(prec=28)
    context.traps[Inexact] = True
    with localcontext(context):
        source = BarDataset(bars)
        instrument = (
            next(
                (row for row in PAPER_INSTRUMENTS if row.instrument_id == bars[0].instrument_id),
                None,
            )
            if bars
            else None
        )
        if (
            not bars
            or instrument is None
            or source.bars != bars
            or source.duplicate_rows
            or any(row.instrument_id != instrument.instrument_id for row in bars)
        ):
            raise ValueError("Native fill source mismatch")
        if type(quantity) is not int or not 1 <= quantity <= (500000 if directional else 1000000):
            raise ValueError("Native quantity exceeds declared full liquidity")
        positions = (
            (-Decimal(quantity), Decimal(0), Decimal(quantity))
            if directional
            else (Decimal(0), Decimal(quantity))
        )
        quantities = (quantity, 2 * quantity) if directional else (quantity,)
        opens = {bar.observed_at: bar.open for bar in bars[1:]}
        position, cash, previous = Decimal(0), Decimal(0), None
        for fill in fills:
            if (
                fill.side not in ("BUY", "SELL")
                or fill.quantity not in quantities
                or fill.fee != costs.fee_per_trade
                or not fill.price.is_finite()
                or fill.price <= 0
                or fill.observed_at not in opens
                or (previous is not None and fill.observed_at <= previous)
            ):
                raise ValueError("Native fill differs from declared replay")
            with localcontext() as rounding:
                rounding.traps[Inexact] = False
                direction = Decimal(1) if fill.side == "BUY" else Decimal(-1)
                increment = (
                    Decimal("0.001") if instrument.quote_currency == "JPY" else Decimal("0.00001")
                )
                price = (
                    opens[fill.observed_at] * (1 + direction * costs.slippage_bps / 10000)
                ).quantize(increment, rounding=ROUND_CEILING if fill.side == "BUY" else ROUND_FLOOR)
            if fill.price != price:
                raise ValueError("Native fill price differs from source-open execution model")
            signed = fill.quantity if fill.side == "BUY" else -fill.quantity
            position += signed
            cash -= signed * fill.price + (fill.fee if fees_in_quote_currency else Decimal(0))
            if position not in positions:
                raise ValueError("Native declared position mismatch")
            previous = fill.observed_at
        return position, cash
