"""Reconcile retained long/flat native bar evidence against its declared source model."""

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
            or result.replay_model != "BAR_OPEN_CLOSE_QUOTES_V1"
            or result.pnl_method != "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
        ):
            raise ValueError("Native bar source/model binding mismatch")
        position, cash = Decimal(0), Decimal(0)
        opens = {bar.observed_at: bar.open for bar in bars[1:]}
        previous = None
        for fill in result.fills:
            if (
                fill.side not in ("BUY", "SELL")
                or fill.quantity != quantity
                or fill.fee != costs.fee_per_trade
                or not fill.price.is_finite()
                or fill.price <= 0
                or fill.observed_at not in opens
                or (previous is not None and fill.observed_at <= previous)
            ):
                raise ValueError("Native fill differs from declared replay")
            # Explicit rounding belongs to the native synthetic quote model.
            with localcontext() as rounding:
                rounding.traps[Inexact] = False
                direction = Decimal(1) if fill.side == "BUY" else Decimal(-1)
                increment = Decimal("0.001") if result.pnl_currency == "JPY" else Decimal("0.00001")
                price = (
                    opens[fill.observed_at] * (1 + direction * costs.slippage_bps / 10000)
                ).quantize(increment, rounding=ROUND_CEILING if fill.side == "BUY" else ROUND_FLOOR)
            if fill.price != price:
                raise ValueError("Native fill price differs from source-open execution model")
            signed = fill.quantity if fill.side == "BUY" else -fill.quantity
            position += signed
            cash -= signed * fill.price + fill.fee
            if position not in (Decimal(0), Decimal(quantity)):
                raise ValueError("Native long-flat position mismatch")
            previous = fill.observed_at
        if (
            type(result.native_order_count) is not int
            or result.native_order_count != len(result.fills)
            or result.final_position != position
            or cash + position * bars[-1].close != result.net_pnl
        ):
            raise ValueError("Native cash-flow ledger mismatch")
