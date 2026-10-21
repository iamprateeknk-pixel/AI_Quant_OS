"""Source-bound recorded-quote fill evidence; does not execute strategy code."""

from datetime import datetime, timezone
from decimal import Context, Decimal, Inexact, localcontext

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.tick_data import TickDataset

MAX_NATIVE_REPLAY_EVENTS = 100_000


def canonical_tick_source(dataset, quantity, costs, *, require_complete_liquidity=True):
    """Snapshot bounded native inputs before a strategy or engine can run."""
    if type(dataset) is not TickDataset or dataset.duplicate_rows:
        raise ValueError("Native tick source has duplicate or unsupported rows")
    if type(quantity) is not int or not 1 <= quantity <= 1000000:
        raise ValueError("Invalid native tick quantity")
    if type(costs) is not BacktestCosts or costs.slippage_bps:
        raise ValueError("Recorded tick replay requires zero synthetic slippage")
    if not 1 <= len(dataset.ticks) <= MAX_NATIVE_REPLAY_EVENTS:
        raise ValueError("Native replay exceeds the 100000 event budget")
    source = TickDataset(tuple(dataset.ticks))
    if source.duplicate_rows:
        raise ValueError("Native replay requires unique ticks")
    identifiers = {row.instrument_id for row in source.ticks}
    if len(identifiers) != 1 or len({row.source_id for row in source.ticks}) != 1:
        raise ValueError("Native tick replay requires one instrument and source")
    metadata = next((p for p in PAPER_INSTRUMENTS if p.instrument_id in identifiers), None)
    if metadata is None:
        raise ValueError("Unknown native paper instrument")
    fee_increment = Decimal(1) if metadata.quote_currency == "JPY" else Decimal(".01")
    if costs.fee_per_trade.quantize(fee_increment, context=Context(prec=28)) != costs.fee_per_trade:
        raise ValueError("Fixed fee exceeds account currency precision")
    increment = Decimal(".001") if metadata.quote_currency == "JPY" else Decimal(".00001")
    previous = None
    for row in source.ticks:
        if previous is not None and row.observed_at <= previous:
            raise ValueError("Recorded tick timestamps must be strictly increasing")
        if any(price.quantize(increment) != price for price in (row.bid, row.ask)):
            raise ValueError("Source price exceeds instrument precision")
        if any(
            size != size.to_integral_value() or size > 1000000
            for size in (row.bid_size, row.ask_size)
        ):
            raise ValueError("Recorded size requires integer units up to 1000000")
        if min(row.bid_size, row.ask_size) <= 0:
            raise ValueError("Recorded quote requires positive displayed liquidity")
        if require_complete_liquidity and min(row.bid_size, row.ask_size) < 2 * quantity:
            raise ValueError("Recorded liquidity cannot cover a complete target reversal")
        previous = row.observed_at
    return source, metadata


def reconcile_tick_fills(
    fills,
    source,
    quantity,
    costs,
    *,
    fees_in_quote_currency=True,
    allow_partial_liquidity=False,
):
    """Check next-quote execution and exact quote-currency cash; no target replay."""
    if type(fills) is not tuple or len(fills) > len(source.ticks) - 1:
        raise ValueError("Native fill count exceeds source events")
    quotes = {row.observed_at: row for row in source.ticks[1:]}
    position, cash, previous = Decimal(0), Decimal(0), None
    consumed: dict[tuple[datetime, str], Decimal] = {}
    for fill in fills:
        if (
            type(fill.observed_at) is not datetime
            or fill.observed_at.tzinfo is None
            or fill.observed_at.utcoffset() != timezone.utc.utcoffset(None)
            or any(
                type(v) is not Decimal or not v.is_finite()
                for v in (fill.price, fill.quantity, fill.fee)
            )
            or fill.side not in ("BUY", "SELL")
            or fill.quantity <= 0
            or fill.quantity != fill.quantity.to_integral_value()
            or fill.quantity > 2 * quantity
            or (
                not allow_partial_liquidity
                and fill.quantity not in (Decimal(quantity), Decimal(2 * quantity))
            )
            or fill.fee != costs.fee_per_trade
            or fill.observed_at not in quotes
            or (previous is not None and fill.observed_at <= previous)
        ):
            raise ValueError("Native fill differs from recorded source model")
        quote = quotes[fill.observed_at]
        if fill.price != (quote.ask if fill.side == "BUY" else quote.bid):
            raise ValueError("Native fill price differs from source quote")
        if allow_partial_liquidity:
            key = (fill.observed_at, fill.side)
            capacity = quote.ask_size if fill.side == "BUY" else quote.bid_size
            consumed[key] = consumed.get(key, Decimal(0)) + fill.quantity
            if consumed[key] > capacity:
                raise ValueError("Native fill exceeds displayed quote-side liquidity")
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        position += signed
        cash -= signed * fill.price + (fill.fee if fees_in_quote_currency else Decimal(0))
        if abs(position) > quantity or (
            not allow_partial_liquidity
            and position not in (-Decimal(quantity), Decimal(0), Decimal(quantity))
        ):
            raise ValueError("Native fill exceeds declared target position")
        previous = fill.observed_at
    return position, cash


def verify_native_tick_result(result, dataset, quantity, costs, *, strategy_ref):
    """Reconcile retained output; strategy correctness/realized PnL needs replay."""
    context = Context(prec=28)
    context.traps[Inexact] = True
    with localcontext(context):
        source, metadata = canonical_tick_source(dataset, quantity, costs)
        if (
            result.schema_version != "1.0"
            or result.engine != "NAUTILUS_BACKTEST"
            or result.engine_version != "1.221.0"
            or result.venue != "PAPER"
            or result.replay_model != "RECORDED_QUOTE_TICKS_V1"
            or result.pnl_method != "EXACT_FILL_CASHFLOWS_PLUS_FINAL_QUOTE_MIDPOINT"
            or result.instrument_id != metadata.instrument_id
            or result.pnl_currency != metadata.quote_currency
            or result.dataset_fingerprint != source.fingerprint
            or result.strategy_ref != strategy_ref
            or result.costs != costs
        ):
            raise ValueError("Native tick source/model binding mismatch")
        position, cash = reconcile_tick_fills(result.fills, source, quantity, costs)
        final = source.ticks[-1]
        mark = (final.bid + final.ask) / 2
        if (
            type(result.native_order_count) is not int
            or result.native_order_count != len(result.fills)
            or any(
                type(v) is not Decimal or not v.is_finite()
                for v in (result.final_position, result.net_pnl, result.native_realized_pnl)
            )
            or result.final_position != position
            or result.net_pnl != cash + position * mark
        ):
            raise ValueError("Native tick cash-flow ledger mismatch")
