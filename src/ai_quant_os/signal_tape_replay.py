"""Native FX replay of JSON target values; no proposed Python is imported or executed."""

from datetime import datetime, timedelta, timezone
from decimal import (
    Context,
    Decimal,
    DecimalException,
    Inexact,
    ROUND_CEILING,
    ROUND_FLOOR,
    localcontext,
)
from typing import Literal

import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import MakerTakerFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.signal_tape_contracts import (
    SignalTape,
    SignalReplayCosts,
    SignalReplayFill,
    SignalReplayReport,
)
from ai_quant_os.validation_package_contracts import ValidationPackage

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> int:
    delta = value - EPOCH
    stamp = (delta.days * 86400 + delta.seconds) * 1000000000 + delta.microseconds * 1000
    if not 0 <= stamp < 2**64 - 1:
        raise ValueError("Signal replay timestamp outside native range")
    return stamp


class _TapeStrategy(Strategy):
    def __init__(
        self,
        instrument,
        targets: dict[int, int],
        quantity: int,
        currency: str,
        initial_balance: Decimal,
        commission_rate: Decimal,
    ):
        super().__init__(
            StrategyConfig(
                strategy_id="SignalTape",
                order_id_tag="067",
                use_uuid_client_order_ids=False,
                log_events=False,
                log_commands=False,
            )
        )
        self.instrument, self.targets, self.quantity, self.currency = (
            instrument,
            targets,
            quantity,
            currency,
        )
        self.position, self.fills, self.failure = Decimal(0), [], None
        self.cash, self.initial_balance, self.commission_rate = (
            Decimal(0),
            initial_balance,
            commission_rate,
        )

    def on_start(self):
        self.subscribe_quote_ticks(self.instrument.id)

    def on_quote_tick(self, tick):
        if self.failure or tick.ts_event not in self.targets:
            return
        delta = self.targets[tick.ts_event] * self.quantity - self.position
        if delta:
            target_position = Decimal(self.targets[tick.ts_event] * self.quantity)
            bid, ask = tick.bid_price.as_decimal(), tick.ask_price.as_decimal()
            price = ask if delta > 0 else bid
            fee = Money(
                price * abs(delta) * self.commission_rate, self.instrument.quote_currency
            ).as_decimal()
            after_cash = self.cash - delta * price - fee
            mark = bid if target_position >= 0 else ask
            equity = self.initial_balance + after_cash + target_position * mark
            if _adds_risk(self.position, target_position) and abs(target_position) * ask > equity:
                self.failure = "Native signal replay denied declared quote collateral"
                return
            self.submit_order(
                self.order_factory.market(
                    instrument_id=self.instrument.id,
                    order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                    quantity=Quantity.from_int(int(abs(delta))),
                )
            )

    def on_order_filled(self, event):
        if event.commission.currency.code != self.currency:
            self.failure = "Native signal commission currency mismatch"
            return
        quantity = event.last_qty.as_decimal()
        side = "BUY" if event.order_side == OrderSide.BUY else "SELL"
        self.position += quantity if side == "BUY" else -quantity
        signed = quantity if side == "BUY" else -quantity
        self.cash -= signed * event.last_px.as_decimal() + event.commission.as_decimal()
        self.fills.append(
            SignalReplayFill(
                observed_at=EPOCH + timedelta(microseconds=event.ts_event // 1000),
                side=side,
                price=event.last_px.as_decimal(),
                quantity=quantity,
                fee=event.commission.as_decimal(),
            )
        )

    def on_order_denied(self, event):
        self.failure = "Native signal replay denied an order"

    def on_order_rejected(self, event):
        self.failure = "Native signal replay rejected an order"


def _adds_risk(position: Decimal, target: Decimal) -> bool:
    return abs(target) > abs(position) or position * target < 0


def _costs(package: ValidationPackage, scenario: str) -> SignalReplayCosts:
    if scenario not in ("BASE", "DECLARED_STRESS"):
        raise ValueError("Unknown declared cost scenario")
    plan = package.plan
    multiplier = Decimal(1) if scenario == "BASE" else plan.cost_stress_multiplier
    context = Context(prec=28)
    context.traps[Inexact] = True
    try:
        with localcontext(context):
            costs = SignalReplayCosts(
                spread_bps=plan.spread_bps * multiplier,
                slippage_bps=plan.slippage_bps * multiplier,
                commission_bps=plan.commission_bps * multiplier,
                multiplier=multiplier,
            )
            impact = (costs.spread_bps / 2 + costs.slippage_bps) / 10000
            fee_rate = costs.commission_bps / 10000
            if not fee_rate.is_finite() or impact >= 1:
                raise ValueError("Declared price impact must preserve positive bids")
    except DecimalException as error:
        raise ValueError("Declared signal costs exceed exact arithmetic precision") from error
    return costs


def _instrument(identifier: str, costs: SignalReplayCosts, initial_balance: Decimal):
    metadata = next(row for row in PAPER_INSTRUMENTS if row.instrument_id == identifier)
    precision = 3 if metadata.quote_currency == "JPY" else 5
    increment = Decimal(1).scaleb(-precision)
    quote = Currency.from_str(metadata.quote_currency)
    if initial_balance.quantize(Decimal(1).scaleb(-quote.precision)) != initial_balance:
        raise ValueError("Starting balance exceeds quote currency precision")
    native_id = InstrumentId.from_str(identifier + ".PAPER")
    instrument = CurrencyPair(
        instrument_id=native_id,
        raw_symbol=Symbol(identifier),
        base_currency=Currency.from_str(metadata.base_currency),
        quote_currency=quote,
        price_precision=precision,
        size_precision=0,
        price_increment=Price.from_str(f"{increment:.{precision}f}"),
        size_increment=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
        margin_init=Decimal(1),
        margin_maint=Decimal(1),
        maker_fee=Decimal(0),
        taker_fee=costs.commission_bps / 10000,
    )
    return instrument, quote, precision, increment


def _quotes(dataset, instrument, precision, increment, costs):
    impact = (costs.spread_bps / 2 + costs.slippage_bps) / 10000
    ticks = []
    for bar in dataset.bars:
        if any(
            value.quantize(increment) != value for value in (bar.open, bar.high, bar.low, bar.close)
        ):
            raise ValueError("Signal source exceeds instrument precision")
        stamp = _timestamp(bar.observed_at)
        for mid, timestamp in ((bar.open, stamp), (bar.close, stamp + 1)):
            bid = (mid * (1 - impact)).quantize(increment, rounding=ROUND_FLOOR)
            ask = (mid * (1 + impact)).quantize(increment, rounding=ROUND_CEILING)
            if bid <= 0:
                raise ValueError("Signal synthetic bid is not positive")
            ticks.append(
                QuoteTick(
                    instrument.id,
                    Price.from_str(f"{bid:.{precision}f}"),
                    Price.from_str(f"{ask:.{precision}f}"),
                    Quantity.from_int(1000000),
                    Quantity.from_int(1000000),
                    timestamp,
                    timestamp,
                )
            )
    return ticks


def replay_signal_tape(
    package: ValidationPackage,
    tape: SignalTape,
    *,
    quantity: int = 1,
    initial_balance: Decimal = Decimal(100000),
    cost_scenario: Literal["BASE", "DECLARED_STRESS"] = "BASE",
) -> SignalReplayReport:
    if type(quantity) is not int or not 1 <= quantity <= 500000:
        raise ValueError("Signal replay quantity exceeds full reversal liquidity")
    if (
        not isinstance(initial_balance, Decimal)
        or not initial_balance.is_finite()
        or not 0 < initial_balance <= 1000000000000
    ):
        raise ValueError("Signal replay starting balance is invalid")
    if nautilus_trader.__version__ != "1.221.0":
        raise ValueError("Signal native engine version mismatch")
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    tape = SignalTape.model_validate_json(tape.model_dump_json())
    dataset = tape.require_package(package)
    with localcontext(Context(prec=28)):
        costs = _costs(package, cost_scenario)
        instrument, quote, precision, increment = _instrument(
            dataset.bars[0].instrument_id, costs, initial_balance
        )
        ticks = _quotes(dataset, instrument, precision, increment, costs)
        targets = {_timestamp(row.execute_at): row.target for row in tape.decisions}
        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id=TraderId("AIQUANT-TAPE-001"), logging=LoggingConfig(bypass_logging=True)
            )
        )
        try:
            engine.add_venue(
                Venue("PAPER"),
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                starting_balances=[Money(initial_balance, quote)],
                base_currency=quote,
                default_leverage=Decimal(1),
                fee_model=MakerTakerFeeModel(),
                use_random_ids=False,
                use_message_queue=False,
                bar_execution=False,
            )
            engine.add_instrument(instrument)
            bridge = _TapeStrategy(
                instrument,
                targets,
                quantity,
                quote.code,
                initial_balance,
                costs.commission_bps / 10000,
            )
            engine.add_strategy(bridge)
            engine.add_data(ticks)
            engine.run()
            if bridge.failure:
                raise ValueError(bridge.failure)
            position = sum(
                (row.signed_decimal_qty() for row in engine.cache.positions_open()), Decimal(0)
            )
            if position != bridge.position:
                raise ValueError("Native signal position/fill mismatch")
            cash = sum(
                (
                    (-row.quantity if row.side == "BUY" else row.quantity) * row.price - row.fee
                    for row in bridge.fills
                ),
                Decimal(0),
            )
            report = SignalReplayReport(
                package_ref=package.package_ref,
                tape=tape,
                instrument_id=dataset.bars[0].instrument_id,
                pnl_currency=quote.code,
                cost_scenario=cost_scenario,
                costs=costs,
                quantity=quantity,
                initial_balance=initial_balance,
                final_mark=dataset.bars[-1].close,
                fills=tuple(bridge.fills),
                native_order_count=len(engine.cache.orders()),
                final_position=position,
                net_pnl=cash + position * dataset.bars[-1].close,
                native_realized_pnl=sum(
                    (
                        row.realized_pnl.as_decimal()
                        for row in engine.cache.positions()
                        if row.realized_pnl is not None
                    ),
                    Decimal(0),
                ),
            )
            verify_signal_replay(package, report)
            return report
        finally:
            engine.dispose()


def verify_signal_replay(package: ValidationPackage, report: SignalReplayReport) -> None:
    """Reconstruct required native fills; does not attest who produced the target values."""
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    report = SignalReplayReport.model_validate_json(report.model_dump_json())
    dataset = report.tape.require_package(package)
    with localcontext(Context(prec=28)):
        expected_costs = _costs(package, report.cost_scenario)
        identifier = dataset.bars[0].instrument_id
        metadata = next(row for row in PAPER_INSTRUMENTS if row.instrument_id == identifier)
        if (
            report.costs != expected_costs
            or report.instrument_id != identifier
            or report.pnl_currency != metadata.quote_currency
            or report.final_mark != dataset.bars[-1].close
        ):
            raise ValueError("Signal replay cost/instrument/final-mark binding mismatch")
        quote = Currency.from_str(metadata.quote_currency)
        increment = Decimal("0.001") if metadata.quote_currency == "JPY" else Decimal("0.00001")
        impact = (expected_costs.spread_bps / 2 + expected_costs.slippage_bps) / 10000
        position, cash, fill_index = Decimal(0), Decimal(0), 0
        for decision, bar in zip(report.tape.decisions, dataset.bars, strict=True):
            target_position = Decimal(decision.target * report.quantity)
            delta = target_position - position
            if delta:
                if fill_index >= len(report.fills):
                    raise ValueError("Signal replay is missing a required target fill")
                fill = report.fills[fill_index]
                buy = delta > 0
                price = (bar.open * (1 + impact if buy else 1 - impact)).quantize(
                    increment, rounding=ROUND_CEILING if buy else ROUND_FLOOR
                )
                fee = Money(
                    price * abs(delta) * expected_costs.commission_bps / 10000, quote
                ).as_decimal()
                if (fill.observed_at, fill.side, fill.quantity, fill.price, fill.fee) != (
                    bar.observed_at,
                    "BUY" if buy else "SELL",
                    abs(delta),
                    price,
                    fee,
                ):
                    raise ValueError("Signal replay fill differs from target/open/cost model")
                cash -= delta * fill.price + fill.fee
                ask = (bar.open * (1 + impact)).quantize(increment, rounding=ROUND_CEILING)
                bid = (bar.open * (1 - impact)).quantize(increment, rounding=ROUND_FLOOR)
                mark = bid if target_position >= 0 else ask
                equity = report.initial_balance + cash + target_position * mark
                if _adds_risk(position, target_position) and abs(target_position) * ask > equity:
                    raise ValueError("Signal replay exceeds declared quote collateral")
                fill_index += 1
            position = target_position
        if fill_index != len(report.fills) or position != report.final_position:
            raise ValueError("Signal replay has unexplained fills or final position")
