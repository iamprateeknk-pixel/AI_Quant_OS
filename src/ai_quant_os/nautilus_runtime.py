"""Pinned native Nautilus replay over synthetic bars or recorded quote ticks."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Literal

import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FixedFeeModel, PerContractFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy as NativeStrategy

from ai_quant_os.backtest import BacktestCosts, Strategy
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.tick_data import Tick, TickDataset
from ai_quant_os.tick_strategy import TickStrategy
from ai_quant_os.native_tick_evidence import canonical_tick_source, verify_native_tick_result

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class NativeFill:
    observed_at: datetime
    side: Literal["BUY", "SELL"]
    price: Decimal
    quantity: Decimal
    fee: Decimal


@dataclass(frozen=True)
class NautilusResult:
    schema_version: Literal["1.0"]
    engine: Literal["NAUTILUS_BACKTEST"]
    engine_version: Literal["1.221.0"]
    replay_model: Literal[
        "BAR_OPEN_CLOSE_QUOTES_V1", "BAR_OPEN_CLOSE_LONG_SHORT_V1", "RECORDED_QUOTE_TICKS_V1"
    ]
    venue: Literal["PAPER"]
    instrument_id: str
    pnl_currency: str
    strategy_ref: str
    dataset_fingerprint: str
    costs: BacktestCosts
    fills: tuple[NativeFill, ...]
    native_order_count: int
    final_position: Decimal
    net_pnl: Decimal
    pnl_method: Literal[
        "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK",
        "EXACT_FILL_CASHFLOWS_PLUS_FINAL_QUOTE_MIDPOINT",
    ]
    native_realized_pnl: Decimal


def _nanoseconds(observed_at: datetime) -> int:
    delta = observed_at.astimezone(timezone.utc) - EPOCH
    value = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    if not 0 <= value < 2**64 - 1:
        raise ValueError("Unsupported native replay timestamp")
    return value


class _ReplayStrategy(NativeStrategy):
    def __init__(
        self, instrument, plugin, phases: dict, quantity: int, targets=(0, 1), *, tag="001"
    ):
        super().__init__(
            StrategyConfig(
                strategy_id="Replay",
                order_id_tag=tag,
                use_uuid_client_order_ids=False,
                log_events=False,
                log_commands=False,
            )
        )
        self.instrument, self.plugin, self.phases = instrument, plugin, phases
        self.trade_quantity = quantity
        self.targets = targets
        self.history: list[Bar | Tick] = []
        self.fills: list[NativeFill] = []
        self.closed_realized: list[tuple[int, Decimal]] = []
        self.position = Decimal(0)
        self.failure: str | None = None

    def on_start(self):
        self.subscribe_quote_ticks(self.instrument.id)

    def on_quote_tick(self, tick):
        phase, bar = self.phases[tick.ts_event]
        if phase == "CLOSE":
            self.history.append(bar)
            return
        if not self.history:
            if phase == "TICK":
                self.history.append(bar)
            return
        if self.failure:
            return
        try:
            target = self.plugin.target_position(tuple(self.history))
        except Exception:
            self.failure = "Strategy plugin failed"
            self.stop()
            return
        if type(target) is not int or target not in self.targets:
            self.failure = "Invalid strategy target"
            self.stop()
            return
        delta = target * self.trade_quantity - self.position
        if delta:
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=Quantity.from_int(int(abs(delta))),
            )
            self.submit_order(order)
        if phase == "TICK":
            self.history.append(bar)

    def on_order_filled(self, event):
        side = "BUY" if event.order_side == OrderSide.BUY else "SELL"
        quantity = event.last_qty.as_decimal()
        self.position += quantity if side == "BUY" else -quantity
        self.fills.append(
            NativeFill(
                EPOCH + timedelta(microseconds=event.ts_event // 1000),
                side,
                event.last_px.as_decimal(),
                quantity,
                event.commission.as_decimal(),
            )
        )

    def on_order_denied(self, event):
        self.failure = "Native backtest denied an order"

    def on_order_rejected(self, event):
        self.failure = "Native backtest rejected an order"

    def on_position_closed(self, event):
        amount = event.realized_pnl
        if (
            event.instrument_id != self.instrument.id
            or amount.currency != self.instrument.quote_currency
            or not amount.as_decimal().is_finite()
            or (self.closed_realized and event.ts_closed <= self.closed_realized[-1][0])
        ):
            self.failure = "Native closed-position observation mismatch"
            return
        self.closed_realized.append((event.ts_closed, amount.as_decimal()))


def run_nautilus_backtest(
    dataset: BarDataset,
    strategy: Strategy,
    costs: BacktestCosts,
    *,
    strategy_ref: str,
    quantity: int = 1,
    initial_balance: Decimal = Decimal(100000),
) -> NautilusResult:
    with localcontext(Context(prec=28)):
        return _run_native_backtest(
            dataset,
            strategy,
            costs,
            strategy_ref=strategy_ref,
            quantity=quantity,
            initial_balance=initial_balance,
        )


def _run_native_backtest(
    dataset: BarDataset,
    strategy: Strategy,
    costs: BacktestCosts,
    *,
    strategy_ref: str,
    quantity: int,
    initial_balance: Decimal,
    replay_model: str = "BAR_OPEN_CLOSE_QUOTES_V1",
    targets: tuple[int, ...] = (0, 1),
) -> NautilusResult:
    if not 1 <= len(dataset.bars) <= 1_000_000:
        raise ValueError("Native replay requires 1..1000000 bars")
    identifiers = {bar.instrument_id for bar in dataset.bars}
    if len(identifiers) != 1:
        raise ValueError("Native replay requires one instrument")
    identifier = next(iter(identifiers))
    metadata, precision, increment, quote_currency, instrument = _prepare_instrument(
        identifier, costs, strategy_ref, quantity, initial_balance
    )
    ticks, phases = _bar_quotes(dataset, instrument, precision, increment, costs.slippage_bps)
    return _execute_native_replay(
        instrument,
        metadata,
        quote_currency,
        strategy,
        costs,
        strategy_ref,
        quantity,
        initial_balance,
        ticks,
        phases,
        dataset.fingerprint,
        dataset.bars[-1].close,
        replay_model,
        targets,
    )


def _bar_quotes(dataset, instrument, precision, increment, slippage_bps):
    for bar in dataset.bars:
        if any(
            price.quantize(increment) != price for price in (bar.open, bar.high, bar.low, bar.close)
        ):
            raise ValueError("Source price exceeds instrument precision")
    slip = slippage_bps / 10000
    native_id = instrument.id
    ticks, phases = [], {}
    for bar in dataset.bars:
        stamp = _nanoseconds(bar.observed_at)
        for phase, mid, timestamp in (("OPEN", bar.open, stamp), ("CLOSE", bar.close, stamp + 1)):
            bid = (mid * (1 - slip)).quantize(increment, rounding=ROUND_FLOOR)
            ask = (mid * (1 + slip)).quantize(increment, rounding=ROUND_CEILING)
            if bid <= 0:
                raise ValueError("Synthetic bid is not positive at instrument precision")
            ticks.append(
                QuoteTick(
                    native_id,
                    Price.from_str(f"{bid:.{precision}f}"),
                    Price.from_str(f"{ask:.{precision}f}"),
                    Quantity.from_int(1_000_000),
                    Quantity.from_int(1_000_000),
                    timestamp,
                    timestamp,
                )
            )
            phases[timestamp] = (phase, bar)
    return ticks, phases


def run_nautilus_directional_backtest(
    dataset: BarDataset,
    strategy: Strategy,
    costs: BacktestCosts,
    *,
    strategy_ref: str,
    quantity: int = 1,
    initial_balance: Decimal = Decimal(100000),
) -> NautilusResult:
    if type(quantity) is not int or not 1 <= quantity <= 500000:
        raise ValueError("Directional quantity exceeds complete-reversal liquidity bound")
    with localcontext(Context(prec=28)):
        return _run_native_backtest(
            dataset,
            strategy,
            costs,
            strategy_ref=strategy_ref,
            quantity=quantity,
            initial_balance=initial_balance,
            replay_model="BAR_OPEN_CLOSE_LONG_SHORT_V1",
            targets=(-1, 0, 1),
        )


def _prepare_instrument(
    identifier, costs, strategy_ref, quantity, initial_balance, *, account_currency=None
):
    if nautilus_trader.__version__ != "1.221.0":
        raise ValueError("Native runtime version does not match pin")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}@\d+\.\d+\.\d+", strategy_ref):
        raise ValueError("Versioned strategy reference required")
    if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
        raise ValueError("Invalid native trade quantity")
    if not initial_balance.is_finite() or not 0 < initial_balance <= 1_000_000_000_000:
        raise ValueError("Invalid native starting balance")
    metadata = next((row for row in PAPER_INSTRUMENTS if row.instrument_id == identifier), None)
    if metadata is None:
        raise ValueError("Unknown native paper instrument")
    precision = 3 if metadata.quote_currency == "JPY" else 5
    increment = Decimal(1).scaleb(-precision)
    quote_currency = Currency.from_str(metadata.quote_currency)
    fee_increment = Decimal(1).scaleb(-(account_currency or quote_currency).precision)
    if costs.fee_per_trade.quantize(fee_increment) != costs.fee_per_trade:
        raise ValueError("Fixed fee exceeds account currency precision")
    if initial_balance.quantize(fee_increment) != initial_balance:
        raise ValueError("Balance exceeds currency precision")
    slip = costs.slippage_bps / 10000
    if slip >= 1:
        raise ValueError("Slippage must preserve positive synthetic bids")
    native_id = InstrumentId.from_str(identifier + ".PAPER")
    instrument = CurrencyPair(
        instrument_id=native_id,
        raw_symbol=Symbol(identifier),
        base_currency=Currency.from_str(metadata.base_currency),
        quote_currency=quote_currency,
        price_precision=precision,
        size_precision=0,
        price_increment=Price.from_str(f"{increment:.{precision}f}"),
        size_increment=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
        margin_init=Decimal(1),
        margin_maint=Decimal(1),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
    )
    return metadata, precision, increment, quote_currency, instrument


def run_nautilus_tick_backtest(
    dataset: TickDataset,
    strategy: TickStrategy,
    costs: BacktestCosts,
    *,
    strategy_ref: str,
    quantity: int = 1,
    initial_balance: Decimal = Decimal(100000),
) -> NautilusResult:
    with localcontext(Context(prec=28)):
        dataset, source_metadata = canonical_tick_source(dataset, quantity, costs)
        metadata, precision, increment, quote_currency, instrument = _prepare_instrument(
            source_metadata.instrument_id, costs, strategy_ref, quantity, initial_balance
        )
        ticks, phases = [], {}
        for row in dataset.ticks:
            timestamp = _nanoseconds(row.observed_at)
            ticks.append(
                QuoteTick(
                    instrument.id,
                    Price.from_str(f"{row.bid:.{precision}f}"),
                    Price.from_str(f"{row.ask:.{precision}f}"),
                    Quantity.from_int(int(row.bid_size)),
                    Quantity.from_int(int(row.ask_size)),
                    timestamp,
                    timestamp,
                )
            )
            phases[timestamp] = ("TICK", row)
        final = dataset.ticks[-1]
        result = _execute_native_replay(
            instrument,
            metadata,
            quote_currency,
            strategy,
            costs,
            strategy_ref,
            quantity,
            initial_balance,
            ticks,
            phases,
            dataset.fingerprint,
            (final.bid + final.ask) / 2,
            "RECORDED_QUOTE_TICKS_V1",
            (-1, 0, 1),
        )
        verify_native_tick_result(result, dataset, quantity, costs, strategy_ref=strategy_ref)
        return result


def _execute_native_replay(
    instrument,
    metadata,
    quote_currency,
    strategy,
    costs,
    strategy_ref,
    quantity,
    initial_balance,
    ticks,
    phases,
    dataset_fingerprint,
    final_mark,
    replay_model,
    targets,
):
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("AIQUANT-001"), logging=LoggingConfig(bypass_logging=True)
        )
    )
    try:
        fee = Money(costs.fee_per_trade, quote_currency)
        fee_model = FixedFeeModel(fee) if costs.fee_per_trade else PerContractFeeModel(fee)
        engine.add_venue(
            Venue("PAPER"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(initial_balance, quote_currency)],
            base_currency=quote_currency,
            default_leverage=Decimal(1),
            fee_model=fee_model,
            use_random_ids=False,
            use_message_queue=False,
            bar_execution=False,
        )
        engine.add_instrument(instrument)
        bridge = _ReplayStrategy(instrument, strategy, phases, quantity, targets)
        engine.add_strategy(bridge)
        engine.add_data(ticks)
        engine.run()
        if bridge.failure:
            raise ValueError(bridge.failure)
        native_position = sum(
            (row.signed_decimal_qty() for row in engine.cache.positions_open()), Decimal(0)
        )
        if native_position != bridge.position:
            raise ValueError("Native position/fill ledger mismatch")
        closed_times, previous_position = [], Decimal(0)
        for fill in bridge.fills:
            position = previous_position + (fill.quantity if fill.side == "BUY" else -fill.quantity)
            if previous_position and (not position or position * previous_position < 0):
                closed_times.append(_nanoseconds(fill.observed_at))
            previous_position = position
        if closed_times != [stamp for stamp, _ in bridge.closed_realized]:
            raise ValueError("Native closed-position event inventory mismatch")
        realized = sum((amount for _, amount in bridge.closed_realized), Decimal(0)) + sum(
            (
                position.realized_pnl.as_decimal()
                for position in engine.cache.positions_open()
                if position.realized_pnl is not None
            ),
            Decimal(0),
        )
        balance = engine.cache.accounts()[0].balance_total(quote_currency).as_decimal()
        if initial_balance + realized != balance:
            raise ValueError("Native realized PnL/account balance mismatch")
        cash = sum(
            (-fill.price * fill.quantity if fill.side == "BUY" else fill.price * fill.quantity)
            - fill.fee
            for fill in bridge.fills
        )
        return NautilusResult(
            "1.0",
            "NAUTILUS_BACKTEST",
            nautilus_trader.__version__,
            replay_model,
            "PAPER",
            metadata.instrument_id,
            metadata.quote_currency,
            strategy_ref,
            dataset_fingerprint,
            costs,
            tuple(bridge.fills),
            len(engine.cache.orders()),
            native_position,
            cash + native_position * final_mark,
            "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
            if replay_model in ("BAR_OPEN_CLOSE_QUOTES_V1", "BAR_OPEN_CLOSE_LONG_SHORT_V1")
            else "EXACT_FILL_CASHFLOWS_PLUS_FINAL_QUOTE_MIDPOINT",
            realized,
        )
    finally:
        engine.dispose()
