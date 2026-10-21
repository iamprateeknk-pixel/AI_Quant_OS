"""Trusted asynchronous recorded quotes in one native USD collateral account."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Context, Decimal, ROUND_CEILING, localcontext
from typing import Literal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FixedFeeModel, PerContractFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.native_portfolio import _PortfolioReplay
from ai_quant_os.native_tick_evidence import canonical_tick_source, reconcile_tick_fills
from ai_quant_os.nautilus_runtime import (
    NativeFill,
    _ReplayStrategy,
    _nanoseconds,
    _prepare_instrument,
)
from ai_quant_os.strategy_contracts import StrategyBinding, StrategyGenome
from ai_quant_os.strategy_registry import prepare_builtin, verify_implementation
from ai_quant_os.tick_data import Tick, TickDataset


@dataclass(frozen=True)
class TickPortfolioInput:
    dataset: TickDataset
    genome: StrategyGenome
    quantity: int


@dataclass(frozen=True)
class TickPortfolioLeg:
    instrument_id: str
    quote_currency: str
    source_ticks: tuple[Tick, ...]
    dataset_fingerprint: str
    binding: StrategyBinding
    quantity: int
    fills: tuple[NativeFill, ...]
    final_position: Decimal
    gross_pnl_quote: Decimal
    gross_pnl_usd: Decimal
    fees_usd: Decimal
    fee_currency: Literal["USD"] = "USD"


@dataclass(frozen=True)
class TickPortfolioReport:
    initial_balance_usd: Decimal
    fee_per_order_usd: Decimal
    max_quote_age_seconds: int
    observed_at: datetime
    legs: tuple[TickPortfolioLeg, ...]
    native_order_count: int
    total_fees_usd: Decimal
    net_pnl_usd: Decimal
    native_balance_usd: Decimal
    schema_version: Literal["1.1"] = "1.1"
    engine: Literal["NAUTILUS_BACKTEST"] = "NAUTILUS_BACKTEST"
    engine_version: Literal["1.221.0"] = "1.221.0"
    venue: Literal["PAPER"] = "PAPER"
    replay_model: Literal["ASYNC_RECORDED_QUOTE_PORTFOLIO_USD_V2"] = (
        "ASYNC_RECORDED_QUOTE_PORTFOLIO_USD_V2"
    )
    liquidity_model: Literal["SOURCE_VISIBLE_SIDE_ORDER_SLICES_V1"] = (
        "SOURCE_VISIBLE_SIDE_ORDER_SLICES_V1"
    )
    decision_model: Literal["ALL_EVENT_QUOTES_BEFORE_PRIOR_HISTORY_TARGETS_V1"] = (
        "ALL_EVENT_QUOTES_BEFORE_PRIOR_HISTORY_TARGETS_V1"
    )
    valuation_model: Literal["FINAL_MIDPOINT_SIGN_FX_DECIMAL28_V1"] = (
        "FINAL_MIDPOINT_SIGN_FX_DECIMAL28_V1"
    )
    collateral_model: Literal["FRESH_GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"] = (
        "FRESH_GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"
    )
    promotion_allowed: Literal[False] = False


def _sources(legs, fee, balance, age):
    if type(legs) is not tuple or not 2 <= len(legs) <= 4:
        raise ValueError("Tick portfolio requires 2..4 unique instruments")
    if type(age) is not int or not 1 <= age <= 300:
        raise ValueError("Invalid source quote age policy")
    if (
        any(type(v) is not Decimal or not v.is_finite() for v in (fee, balance))
        or fee < 0
        or not 0 < balance <= Decimal("1e12")
        or fee.quantize(Decimal(".01")) != fee
        or balance.quantize(Decimal(".01")) != balance
    ):
        raise ValueError("Invalid USD balance/fee policy")
    prepared = []
    for row in legs:
        if type(row) is not TickPortfolioInput or row.genome.input_kind != "TICK":
            raise ValueError("Trusted tick genomes required")
        source, metadata = canonical_tick_source(
            row.dataset,
            row.quantity,
            BacktestCosts(Decimal(0), Decimal(0)),
            require_complete_liquidity=False,
        )
        if len(source.ticks) > 10000:
            raise ValueError("Tick portfolio source capacity exceeded")
        prepared.append((TickPortfolioInput(source, row.genome, row.quantity), metadata))
    prepared.sort(key=lambda row: row[1].instrument_id)
    names = [metadata.instrument_id for _, metadata in prepared]
    if len(set(names)) != len(names):
        raise ValueError("Tick portfolio requires unique instruments")
    if any(p.quote_currency == "JPY" for _, p in prepared) and "USDJPY" not in names:
        raise ValueError("USDJPY quote leg required for JPY conversion")
    return tuple(prepared)


class _TickReplay(_PortfolioReplay):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._displayed_quote = None
        self._remaining_bid_size = 0
        self._remaining_ask_size = 0

    def on_quote_tick(self, tick):
        phase, row = self.phases[tick.ts_event]
        if phase == "PRICE":
            self._displayed_quote = row
            self._remaining_bid_size = int(row.bid_size)
            self._remaining_ask_size = int(row.ask_size)
        super().on_quote_tick(tick)

    def submit_order(self, order, **kwargs):
        if self._displayed_quote is None:
            return
        is_buy = order.side == OrderSide.BUY
        available = self._remaining_ask_size if is_buy else self._remaining_bid_size
        requested = int(order.quantity.as_decimal())
        quantity = min(requested, available)
        if quantity <= 0:
            return
        if quantity < requested:
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=order.side,
                quantity=Quantity.from_int(quantity),
            )
        issue = self.collateral_check(self, order)
        if issue is not None:
            self.failure = issue
            self.stop()
            return
        _ReplayStrategy.submit_order(self, order, **kwargs)
        if self.failure is None:
            if is_buy:
                self._remaining_ask_size -= quantity
            else:
                self._remaining_bid_size -= quantity


def _quote(engine, prepared, identifier, now, age):
    _, _, _, instrument, bridge = next(
        row for row in prepared if row[2].instrument_id == identifier
    )
    quote = engine.cache.quote_tick(instrument.id)
    if quote is None:
        raise ValueError("Native source quote unavailable")
    stamp = bridge.phases[quote.ts_event][1].observed_at
    elapsed = (now - stamp).total_seconds()
    if not 0 <= elapsed <= age:
        raise ValueError("Native source quote stale or future")
    return quote


def _collateral(engine, prepared, active, order, fee, age):
    try:
        now = active.phases[engine.cache.quote_tick(active.instrument.id).ts_event][1].observed_at
        target = active.position + order.quantity.as_decimal() * (
            1 if order.side == OrderSide.BUY else -1
        )
        if abs(target) < abs(active.position):
            identifier = next(
                metadata.instrument_id for _, _, metadata, _, bridge in prepared if bridge is active
            )
            _quote(engine, prepared, identifier, now, age)
            return None
        with localcontext(Context(prec=28, rounding=ROUND_CEILING)):
            current, proposed = Decimal(0), Decimal(0)
            for _, _, metadata, _, bridge in prepared:
                target = bridge.position
                if bridge is active:
                    target += order.quantity.as_decimal() * (
                        1 if order.side == OrderSide.BUY else -1
                    )
                if not target and not bridge.position:
                    continue
                quote = _quote(engine, prepared, metadata.instrument_id, now, age)
                unit = quote.ask_price.as_decimal()
                if metadata.quote_currency == "JPY":
                    unit /= _quote(engine, prepared, "USDJPY", now, age).bid_price.as_decimal()
                current += abs(bridge.position) * unit
                proposed += abs(target) * unit
            balance = (
                engine.cache.accounts()[0].balance_total(Currency.from_str("USD")).as_decimal()
            )
            return (
                None
                if proposed < current or proposed + fee <= balance
                else "Native tick portfolio denied shared collateral"
            )
    except (ValueError, KeyError):
        return "Native source quote stale, future or unavailable"


def _usd(amount, currency, legs):
    if currency == "USD":
        return amount
    final = next(row for row in legs if row.instrument_id == "USDJPY").source_ticks[-1]
    return amount / (final.ask if amount >= 0 else final.bid)


def run_native_tick_portfolio(
    legs,
    registry,
    *,
    fee_per_order_usd=Decimal(0),
    initial_balance_usd=Decimal(100000),
    max_quote_age_seconds=3,
):
    with localcontext(Context(prec=28)):
        sources = _sources(legs, fee_per_order_usd, initial_balance_usd, max_quote_age_seconds)
        usd = Currency.from_str("USD")
        prepared = []
        ticks = []
        for index, (source, metadata) in enumerate(sources, 1):
            plugin, binding = prepare_builtin(source.genome, registry)
            _, precision, _, _, instrument = _prepare_instrument(
                metadata.instrument_id,
                BacktestCosts(Decimal(0), fee_per_order_usd),
                source.genome.strategy_ref,
                source.quantity,
                initial_balance_usd,
                account_currency=usd,
            )
            phases = {}
            for row in source.dataset.ticks:
                timestamp = _nanoseconds(row.observed_at)
                # Register every same-time source quote before any decision; the second
                # event uses the same original quote and only a one-nanosecond priority.
                for stamp, phase in ((timestamp, "PRICE"), (timestamp + 1, "TICK")):
                    ticks.append(
                        QuoteTick(
                            instrument.id,
                            Price.from_str(f"{row.bid:.{precision}f}"),
                            Price.from_str(f"{row.ask:.{precision}f}"),
                            Quantity.from_int(int(row.bid_size)),
                            Quantity.from_int(int(row.ask_size)),
                            stamp,
                            stamp,
                        )
                    )
                    phases[stamp] = (phase, row)
            bridge = _TickReplay(
                instrument, plugin, phases, source.quantity, (-1, 0, 1), tag=f"{index:03}"
            )
            prepared.append((source, binding, metadata, instrument, bridge))
        ticks.sort(key=lambda row: (row.ts_event, str(row.instrument_id)))
        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("AIQUANT-001"), logging=LoggingConfig(bypass_logging=True)
            )
        )
        try:
            commission = Money(fee_per_order_usd, usd)
            engine.add_venue(
                Venue("PAPER"),
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                starting_balances=[Money(initial_balance_usd, usd)],
                base_currency=usd,
                default_leverage=Decimal(1),
                fee_model=FixedFeeModel(commission)
                if fee_per_order_usd
                else PerContractFeeModel(commission),
                use_random_ids=False,
                use_message_queue=False,
                bar_execution=False,
            )
            for _, _, _, instrument, bridge in prepared:
                bridge.collateral_check = lambda active, order: _collateral(
                    engine, prepared, active, order, fee_per_order_usd, max_quote_age_seconds
                )
                engine.add_instrument(instrument)
                engine.add_strategy(bridge)
            engine.add_data(ticks)
            engine.run()
            rows = []
            for source, binding, metadata, instrument, bridge in prepared:
                if bridge.failure:
                    raise ValueError(bridge.failure)
                position = sum(
                    (
                        p.signed_decimal_qty()
                        for p in engine.cache.positions_open()
                        if p.instrument_id == instrument.id
                    ),
                    Decimal(0),
                )
                if position != bridge.position:
                    raise ValueError("Native tick portfolio position/fill mismatch")
                verify_implementation(binding.implementation)
                if registry.get(binding.genome.strategy_ref) != binding.implementation:
                    raise ValueError("Tick portfolio implementation changed")
                fills = tuple(bridge.fills)
                expected, cash = reconcile_tick_fills(
                    fills,
                    source.dataset,
                    source.quantity,
                    BacktestCosts(Decimal(0), fee_per_order_usd),
                    fees_in_quote_currency=False,
                    allow_partial_liquidity=True,
                )
                if position != expected:
                    raise ValueError("Tick portfolio position mismatch")
                final = source.dataset.ticks[-1]
                rows.append(
                    TickPortfolioLeg(
                        metadata.instrument_id,
                        metadata.quote_currency,
                        source.dataset.ticks,
                        source.dataset.fingerprint,
                        binding,
                        source.quantity,
                        fills,
                        position,
                        cash + position * (final.bid + final.ask) / 2,
                        Decimal(0),
                        sum((f.fee for f in fills), Decimal(0)),
                    )
                )
            complete = tuple(
                replace(row, gross_pnl_usd=_usd(row.gross_pnl_quote, row.quote_currency, rows))
                for row in rows
            )
            fees = sum((row.fees_usd for row in complete), Decimal(0))
            report = TickPortfolioReport(
                initial_balance_usd,
                fee_per_order_usd,
                max_quote_age_seconds,
                max(row.source_ticks[-1].observed_at for row in complete),
                complete,
                len(engine.cache.orders()),
                fees,
                sum((row.gross_pnl_usd for row in complete), Decimal(0)) - fees,
                engine.cache.accounts()[0].balance_total(usd).as_decimal(),
            )
            verify_tick_portfolio_report(report)
            return report
        finally:
            engine.dispose()


def verify_tick_portfolio_report(report):
    with localcontext(Context(prec=28)):
        sources = _sources(
            tuple(
                TickPortfolioInput(TickDataset(row.source_ticks), row.binding.genome, row.quantity)
                for row in report.legs
            ),
            report.fee_per_order_usd,
            report.initial_balance_usd,
            report.max_quote_age_seconds,
        )
        if (
            report.schema_version != "1.1"
            or report.engine != "NAUTILUS_BACKTEST"
            or report.engine_version != "1.221.0"
            or report.venue != "PAPER"
            or report.replay_model != "ASYNC_RECORDED_QUOTE_PORTFOLIO_USD_V2"
            or report.liquidity_model != "SOURCE_VISIBLE_SIDE_ORDER_SLICES_V1"
            or report.decision_model != "ALL_EVENT_QUOTES_BEFORE_PRIOR_HISTORY_TARGETS_V1"
            or report.valuation_model != "FINAL_MIDPOINT_SIGN_FX_DECIMAL28_V1"
            or report.collateral_model != "FRESH_GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"
            or report.promotion_allowed is not False
        ):
            raise ValueError("Tick portfolio model mismatch")
        if [r.instrument_id for r in report.legs] != [
            m.instrument_id for _, m in sources
        ] or report.observed_at != max(r.source_ticks[-1].observed_at for r in report.legs):
            raise ValueError("Tick portfolio source order/time mismatch")
        for row, (source, metadata) in zip(report.legs, sources, strict=True):
            if any(
                type(value) is not Decimal or not value.is_finite()
                for value in (
                    row.final_position,
                    row.gross_pnl_quote,
                    row.gross_pnl_usd,
                    row.fees_usd,
                )
            ):
                raise ValueError("Tick portfolio amounts require finite Decimals")
            if (
                row.source_ticks != source.dataset.ticks
                or row.quote_currency != metadata.quote_currency
                or row.fee_currency != "USD"
                or row.dataset_fingerprint != source.dataset.fingerprint
                or not 0
                <= (report.observed_at - row.source_ticks[-1].observed_at).total_seconds()
                <= report.max_quote_age_seconds
            ):
                raise ValueError("Tick portfolio final source stale or changed")
            position, cash = reconcile_tick_fills(
                row.fills,
                source.dataset,
                row.quantity,
                BacktestCosts(Decimal(0), report.fee_per_order_usd),
                fees_in_quote_currency=False,
                allow_partial_liquidity=True,
            )
            final = row.source_ticks[-1]
            if (
                row.final_position != position
                or row.gross_pnl_quote != cash + position * (final.bid + final.ask) / 2
                or row.gross_pnl_usd != _usd(row.gross_pnl_quote, row.quote_currency, report.legs)
                or row.fees_usd != sum((f.fee for f in row.fills), Decimal(0))
            ):
                raise ValueError("Tick portfolio source/cash mismatch")
        if (
            type(report.native_order_count) is not int
            or report.native_order_count != sum(len(r.fills) for r in report.legs)
            or type(report.total_fees_usd) is not Decimal
            or report.total_fees_usd != sum((r.fees_usd for r in report.legs), Decimal(0))
            or type(report.net_pnl_usd) is not Decimal
            or report.net_pnl_usd
            != sum((r.gross_pnl_usd - r.fees_usd for r in report.legs), Decimal(0))
            or type(report.native_balance_usd) is not Decimal
            or not report.native_balance_usd.is_finite()
        ):
            raise ValueError("Tick portfolio total mismatch")
