"""Trusted synchronized FX strategies sharing one native USD margin account."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Callable, Literal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FixedFeeModel, PerContractFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money, Quantity
from nautilus_trader.model.data import QuoteTick

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.directional_strategy import DIRECTIONAL_REF
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.native_bar_evidence import reconcile_bar_fills
from ai_quant_os.nautilus_runtime import (
    NativeFill,
    _ReplayStrategy,
    _bar_quotes,
    _prepare_instrument,
)
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.strategy_contracts import StrategyBinding, StrategyGenome
from ai_quant_os.strategy_registry import prepare_builtin, verify_implementation


@dataclass(frozen=True)
class PortfolioInput:
    dataset: BarDataset
    genome: StrategyGenome
    quantity: int
    slippage_bps: Decimal = Decimal(0)


@dataclass(frozen=True)
class PortfolioLeg:
    instrument_id: str
    quote_currency: str
    source_bars: tuple[Bar, ...]
    dataset_fingerprint: str
    binding: StrategyBinding
    quantity: int
    slippage_bps: Decimal
    fills: tuple[NativeFill, ...]
    final_position: Decimal
    gross_pnl_quote: Decimal
    gross_pnl_usd: Decimal
    fees_usd: Decimal
    fee_currency: Literal["USD"] = "USD"


@dataclass(frozen=True)
class MaintenanceLiquidation:
    triggered_at: datetime
    equity_usd: Decimal
    required_margin_usd: Decimal
    instruments: tuple[str, ...]

    def __post_init__(self):
        if (
            not isinstance(self.triggered_at, datetime)
            or self.triggered_at.tzinfo is None
            or self.triggered_at.utcoffset() is None
        ):
            raise ValueError("Maintenance liquidation time must be timezone-aware")
        object.__setattr__(self, "triggered_at", self.triggered_at.astimezone(timezone.utc))
        if (
            type(self.equity_usd) is not Decimal
            or not self.equity_usd.is_finite()
            or type(self.required_margin_usd) is not Decimal
            or not self.required_margin_usd.is_finite()
            or self.required_margin_usd <= 0
            or self.equity_usd >= self.required_margin_usd
            or type(self.instruments) is not tuple
            or not self.instruments
            or any(not isinstance(value, str) or not value for value in self.instruments)
            or tuple(sorted(set(self.instruments))) != self.instruments
        ):
            raise ValueError("Invalid maintenance liquidation evidence")


@dataclass(frozen=True)
class PortfolioReport:
    initial_balance_usd: Decimal
    fee_per_order_usd: Decimal
    legs: tuple[PortfolioLeg, ...]
    native_order_count: int
    total_fees_usd: Decimal
    net_pnl_usd: Decimal
    native_balance_usd: Decimal
    schema_version: Literal["1.0", "1.1"] = "1.1"
    engine: Literal["NAUTILUS_BACKTEST"] = "NAUTILUS_BACKTEST"
    venue: Literal["PAPER"] = "PAPER"
    engine_version: Literal["1.221.0"] = "1.221.0"
    replay_model: Literal[
        "SYNCHRONIZED_BAR_PORTFOLIO_USD_V1", "SYNCHRONIZED_BAR_PORTFOLIO_USD_V2"
    ] = "SYNCHRONIZED_BAR_PORTFOLIO_USD_V2"
    valuation_model: Literal["FINAL_QUOTE_PNL_SIGN_FX_DECIMAL28_V1"] = (
        "FINAL_QUOTE_PNL_SIGN_FX_DECIMAL28_V1"
    )
    promotion_allowed: Literal[False] = False
    collateral_model: Literal["GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"] = (
        "GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"
    )
    decision_model: Literal["ALL_CURRENT_OPENS_BEFORE_CAUSAL_DECISIONS_V1"] = (
        "ALL_CURRENT_OPENS_BEFORE_CAUSAL_DECISIONS_V1"
    )
    maintenance_model: Literal["ADVERSE_QUOTE_NATIVE_MAINTENANCE_MARGIN_LIQUIDATION_V1"] = (
        "ADVERSE_QUOTE_NATIVE_MAINTENANCE_MARGIN_LIQUIDATION_V1"
    )
    liquidation: MaintenanceLiquidation | None = None


class _PortfolioReplay(_ReplayStrategy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.maintenance_check: Callable[[int], MaintenanceLiquidation | None] | None = None
        self.liquidated = False

    def on_quote_tick(self, tick):
        phase, _ = self.phases[tick.ts_event]
        if phase == "PRICE":
            return
        if phase == "OPEN" and self.maintenance_check is not None:
            self.liquidated = self.maintenance_check(tick.ts_event) is not None
        if self.liquidated:
            if self.position:
                from nautilus_trader.model.enums import OrderSide

                order = self.order_factory.market(
                    instrument_id=self.instrument.id,
                    order_side=OrderSide.SELL if self.position > 0 else OrderSide.BUY,
                    quantity=Quantity.from_int(int(abs(self.position))),
                )
                self.submit_order(order)
            return
        super().on_quote_tick(tick)

    def submit_order(self, order, **kwargs):
        if not self.collateral_check(self, order):
            self.failure = "Native portfolio denied shared collateral"
            self.stop()
            return
        super().submit_order(order, **kwargs)

    def on_order_filled(self, event):
        if event.commission.currency != Currency.from_str("USD"):
            self.failure = "Native portfolio commission currency mismatch"
        super().on_order_filled(event)


def _synchronized_sources(legs):
    if type(legs) is not tuple or not 2 <= len(legs) <= 4:
        raise ValueError("Native portfolio requires 2..4 unique instruments")
    snapshots = []
    for leg in legs:
        if type(leg) is not PortfolioInput or leg.genome.input_kind != "BAR":
            raise ValueError("Native portfolio requires trusted bar genomes")
        source = BarDataset(tuple(leg.dataset.bars))
        if (
            not 2 <= len(source.bars) <= 4096
            or source.duplicate_rows
            or len({row.instrument_id for row in source.bars}) != 1
        ):
            raise ValueError("Native portfolio requires bounded single-instrument sources")
        directional = leg.genome.strategy_ref == DIRECTIONAL_REF
        if type(leg.quantity) is not int or not 1 <= leg.quantity <= (
            500000 if directional else 1000000
        ):
            raise ValueError("Native portfolio quantity exceeds complete liquidity")
        snapshots.append(PortfolioInput(source, leg.genome, leg.quantity, leg.slippage_bps))
    snapshots.sort(key=lambda row: row.dataset.bars[0].instrument_id)
    identifiers = [row.dataset.bars[0].instrument_id for row in snapshots]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Native portfolio requires unique instruments")
    calendar = tuple(row.observed_at for row in snapshots[0].dataset.bars)
    if any(tuple(row.observed_at for row in item.dataset.bars) != calendar for item in snapshots):
        raise ValueError("Native portfolio sources must be synchronized")
    if (
        any(identifier.endswith("JPY") for identifier in identifiers)
        and "USDJPY" not in identifiers
    ):
        raise ValueError("USDJPY source leg is required for JPY conversion")
    return tuple(snapshots)


def _usd_value(amount, quote_currency, legs):
    if quote_currency == "USD":
        return amount
    usd_jpy = next(row for row in legs if row.instrument_id == "USDJPY")
    close = usd_jpy.source_bars[-1].close
    slip = usd_jpy.slippage_bps / 10000
    bid = (close * (1 - slip)).quantize(Decimal(".001"), rounding=ROUND_FLOOR)
    ask = (close * (1 + slip)).quantize(Decimal(".001"), rounding=ROUND_CEILING)
    return amount / (ask if amount >= 0 else bid)


def _quote_value_usd(amount, quote_currency, usd_jpy_quote):
    if quote_currency == "USD":
        return amount
    if quote_currency != "JPY" or usd_jpy_quote is None:
        raise ValueError("Native maintenance conversion requires current USDJPY evidence")
    bid, ask = usd_jpy_quote
    denominator = ask if amount >= 0 else bid
    return amount / denominator


def _maintenance_metrics(initial_balance_usd, legs, usd_jpy_quote):
    equity = initial_balance_usd
    required_margin = Decimal(0)
    open_instruments = []
    with localcontext(Context(prec=28)):
        for instrument_id, currency, position, fills, bid, ask, ratio in legs:
            cash_flow = sum(
                (fill.quantity * fill.price * (1 if fill.side == "SELL" else -1) for fill in fills),
                Decimal(0),
            )
            equity -= sum((fill.fee for fill in fills), Decimal(0))
            marked_cash = cash_flow
            if position:
                open_instruments.append(instrument_id)
                marked_cash += position * (bid if position > 0 else ask)
                maintenance_quote = abs(position) * ask * ratio
                if currency == "JPY":
                    if usd_jpy_quote is None:
                        raise ValueError(
                            "Native maintenance conversion requires current USDJPY evidence"
                        )
                    required_margin += maintenance_quote / usd_jpy_quote[0]
                else:
                    required_margin += _quote_value_usd(maintenance_quote, currency, usd_jpy_quote)
            equity += _quote_value_usd(marked_cash, currency, usd_jpy_quote)
    return equity, required_margin, tuple(sorted(open_instruments))


def _synthetic_open_quote(bar, quote_currency, slippage_bps):
    precision = 3 if quote_currency == "JPY" else 5
    increment = Decimal(1).scaleb(-precision)
    slip = slippage_bps / 10000
    bid = (bar.open * (1 - slip)).quantize(increment, rounding=ROUND_FLOOR)
    ask = (bar.open * (1 + slip)).quantize(increment, rounding=ROUND_CEILING)
    if bid <= 0:
        raise ValueError("Invalid retained maintenance quote")
    return bid, ask


class _MaintenanceController:
    def __init__(self, engine, prepared, initial_balance_usd):
        self.engine = engine
        self.prepared = prepared
        self.initial_balance_usd = initial_balance_usd
        self.liquidation: MaintenanceLiquidation | None = None

    def check(self, ts_event):
        if self.liquidation is not None:
            return self.liquidation
        open_bridges = tuple(row[4] for row in self.prepared if row[4].position)
        if not open_bridges:
            return None
        quotes = tuple((row, self.engine.cache.quote_tick(row[3].id)) for row in self.prepared)
        if any(quote is None for _, quote in quotes):
            raise ValueError("Native maintenance check is missing a current synchronized quote")
        quote_times = tuple(quote.ts_event for _, quote in quotes)
        if max(quote_times) - min(quote_times) > 1:
            raise ValueError("Native maintenance check requires synchronized current quotes")
        usd_jpy_quote = next(
            (quote for row, quote in quotes if row[2].instrument_id == "USDJPY"), None
        )
        metrics = tuple(
            (
                row[2].instrument_id,
                row[2].quote_currency,
                row[4].position,
                tuple(row[4].fills),
                quote.bid_price.as_decimal(),
                quote.ask_price.as_decimal(),
                row[3].margin_maint,
            )
            for row, quote in quotes
        )
        equity, required_margin, open_instruments = _maintenance_metrics(
            self.initial_balance_usd,
            metrics,
            None
            if usd_jpy_quote is None
            else (usd_jpy_quote.bid_price.as_decimal(), usd_jpy_quote.ask_price.as_decimal()),
        )
        if required_margin <= 0 or equity >= required_margin:
            return None
        timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=ts_event // 1000
        )
        self.liquidation = MaintenanceLiquidation(
            timestamp,
            equity,
            required_margin,
            open_instruments,
        )
        return self.liquidation


def run_native_portfolio(
    legs, registry, *, fee_per_order_usd=Decimal(0), initial_balance_usd=Decimal(100000)
):
    with localcontext(Context(prec=28)):
        return _run(legs, registry, fee_per_order_usd, initial_balance_usd)


def _run(legs, registry, fee, balance):
    sources = _synchronized_sources(legs)
    usd = Currency.from_str("USD")
    prepared, ticks = [], []
    for index, source in enumerate(sources, 1):
        plugin, binding = prepare_builtin(source.genome, registry)
        identifier = source.dataset.bars[0].instrument_id
        costs = BacktestCosts(source.slippage_bps, fee)
        metadata, precision, increment, quote, instrument = _prepare_instrument(
            identifier,
            costs,
            source.genome.strategy_ref,
            source.quantity,
            balance,
            account_currency=usd,
        )
        quotes, phases = _bar_quotes(
            source.dataset, instrument, precision, increment, source.slippage_bps
        )
        portfolio_quotes, portfolio_phases = [], {}
        for tick in quotes:
            phase, bar = phases[tick.ts_event]
            events = (
                ((tick.ts_event, "PRICE"), (tick.ts_event + 1, "OPEN"))
                if phase == "OPEN"
                else ((tick.ts_event + 1, "CLOSE"),)
            )
            for stamp, kind in events:
                portfolio_quotes.append(
                    QuoteTick(
                        tick.instrument_id,
                        tick.bid_price,
                        tick.ask_price,
                        tick.bid_size,
                        tick.ask_size,
                        stamp,
                        stamp,
                    )
                )
                portfolio_phases[stamp] = (kind, bar)
        bridge = _PortfolioReplay(
            instrument,
            plugin,
            portfolio_phases,
            source.quantity,
            (-1, 0, 1) if source.genome.strategy_ref == DIRECTIONAL_REF else (0, 1),
            tag=f"{index:03}",
        )
        prepared.append((source, binding, metadata, instrument, bridge))
        ticks.extend(portfolio_quotes)
    # Equal event times follow canonical instrument order, with OPEN before CLOSE.
    ticks.sort(key=lambda row: (row.ts_event, str(row.instrument_id)))
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("AIQUANT-001"), logging=LoggingConfig(bypass_logging=True)
        )
    )
    try:
        commission = Money(fee, usd)
        engine.add_venue(
            Venue("PAPER"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(balance, usd)],
            base_currency=usd,
            default_leverage=Decimal(1),
            fee_model=FixedFeeModel(commission) if fee else PerContractFeeModel(commission),
            use_random_ids=False,
            use_message_queue=False,
            bar_execution=False,
        )
        maintenance = _MaintenanceController(engine, prepared, balance)
        for _, _, _, instrument, bridge in prepared:
            bridge.maintenance_check = maintenance.check
            bridge.collateral_check = lambda active, order: _collateral_admitted(
                engine, prepared, active, order, fee
            )
            engine.add_instrument(instrument)
            engine.add_strategy(bridge)
        engine.add_data(ticks)
        engine.run()
        rows = []
        for source, binding, metadata, instrument, bridge in prepared:
            if bridge.failure:
                raise ValueError(bridge.failure)
            native_position = sum(
                (
                    p.signed_decimal_qty()
                    for p in engine.cache.positions_open()
                    if p.instrument_id == instrument.id
                ),
                Decimal(0),
            )
            if native_position != bridge.position:
                raise ValueError("Native portfolio position/fill mismatch")
            verify_implementation(binding.implementation)
            if registry.get(binding.genome.strategy_ref) != binding.implementation:
                raise ValueError("Native portfolio implementation changed")
            fills = tuple(bridge.fills)
            position, cash = reconcile_bar_fills(
                fills,
                source.dataset.bars,
                source.quantity,
                BacktestCosts(source.slippage_bps, fee),
                directional=source.genome.strategy_ref == DIRECTIONAL_REF,
                fees_in_quote_currency=False,
            )
            rows.append(
                PortfolioLeg(
                    metadata.instrument_id,
                    metadata.quote_currency,
                    source.dataset.bars,
                    source.dataset.fingerprint,
                    binding,
                    source.quantity,
                    source.slippage_bps,
                    fills,
                    position,
                    cash + position * source.dataset.bars[-1].close,
                    Decimal(0),
                    sum((f.fee for f in fills), Decimal(0)),
                )
            )
        from dataclasses import replace

        completed = tuple(
            replace(row, gross_pnl_usd=_usd_value(row.gross_pnl_quote, row.quote_currency, rows))
            for row in rows
        )
        account = engine.cache.accounts()[0]
        total_fees = sum((row.fees_usd for row in completed), Decimal(0))
        report = PortfolioReport(
            balance,
            fee,
            completed,
            len(engine.cache.orders()),
            total_fees,
            sum((row.gross_pnl_usd - row.fees_usd for row in completed), Decimal(0)),
            account.balance_total(usd).as_decimal(),
            liquidation=maintenance.liquidation,
        )
        verify_portfolio_report(report)
        return report
    finally:
        engine.dispose()


def verify_portfolio_report(report):
    """Verify retained mark arithmetic; the observed native balance needs engine replay."""
    with localcontext(Context(prec=28)):
        if (
            type(report.initial_balance_usd) is not Decimal
            or not report.initial_balance_usd.is_finite()
            or not 0 < report.initial_balance_usd <= Decimal("1e12")
            or report.initial_balance_usd.quantize(Decimal(".01")) != report.initial_balance_usd
            or type(report.fee_per_order_usd) is not Decimal
            or not report.fee_per_order_usd.is_finite()
            or report.fee_per_order_usd < 0
            or report.fee_per_order_usd.quantize(Decimal(".01")) != report.fee_per_order_usd
        ):
            raise ValueError("Native portfolio balance/fee policy mismatch")
        expected_replay = (
            "SYNCHRONIZED_BAR_PORTFOLIO_USD_V1"
            if report.schema_version == "1.0"
            else "SYNCHRONIZED_BAR_PORTFOLIO_USD_V2"
        )
        if (
            report.schema_version not in ("1.0", "1.1")
            or report.engine != "NAUTILUS_BACKTEST"
            or report.venue != "PAPER"
            or report.engine_version != "1.221.0"
            or report.replay_model != expected_replay
            or report.valuation_model != "FINAL_QUOTE_PNL_SIGN_FX_DECIMAL28_V1"
            or report.promotion_allowed is not False
            or report.collateral_model != "GROSS_TARGET_NOTIONAL_USD_NATIVE_BALANCE_V1"
            or report.decision_model != "ALL_CURRENT_OPENS_BEFORE_CAUSAL_DECISIONS_V1"
            or (report.schema_version == "1.0" and report.liquidation is not None)
            or (
                report.schema_version == "1.1"
                and report.maintenance_model
                != "ADVERSE_QUOTE_NATIVE_MAINTENANCE_MARGIN_LIQUIDATION_V1"
            )
            or (
                report.liquidation is not None
                and type(report.liquidation) is not MaintenanceLiquidation
            )
        ):
            raise ValueError("Native portfolio model mismatch")
        sources = tuple(
            PortfolioInput(
                BarDataset(row.source_bars), row.binding.genome, row.quantity, row.slippage_bps
            )
            for row in report.legs
        )
        ordered = _synchronized_sources(sources)
        if [row.instrument_id for row in report.legs] != [
            s.dataset.bars[0].instrument_id for s in ordered
        ]:
            raise ValueError("Native portfolio source order mismatch")
        for row in report.legs:
            metadata = next(
                item for item in PAPER_INSTRUMENTS if item.instrument_id == row.instrument_id
            )
            if (
                row.quote_currency != metadata.quote_currency
                or row.fee_currency != "USD"
                or row.dataset_fingerprint != BarDataset(row.source_bars).fingerprint
            ):
                raise ValueError("Native portfolio currency/source mismatch")
            position, cash = reconcile_bar_fills(
                row.fills,
                row.source_bars,
                row.quantity,
                BacktestCosts(row.slippage_bps, report.fee_per_order_usd),
                directional=row.binding.genome.strategy_ref == DIRECTIONAL_REF,
                fees_in_quote_currency=False,
            )
            if (
                position != row.final_position
                or cash + position * row.source_bars[-1].close != row.gross_pnl_quote
                or row.gross_pnl_usd
                != _usd_value(row.gross_pnl_quote, row.quote_currency, report.legs)
                or row.fees_usd != sum((f.fee for f in row.fills), Decimal(0))
            ):
                raise ValueError("Native portfolio cash-flow ledger mismatch")
        if (
            type(report.native_order_count) is not int
            or report.native_order_count != sum(len(row.fills) for row in report.legs)
            or report.total_fees_usd != sum((row.fees_usd for row in report.legs), Decimal(0))
            or report.net_pnl_usd
            != sum((row.gross_pnl_usd - row.fees_usd for row in report.legs), Decimal(0))
            or not report.native_balance_usd.is_finite()
        ):
            raise ValueError("Native portfolio total mismatch")
        if report.liquidation is not None:
            positions_at_trigger = {}
            maintenance_legs = []
            usd_jpy_quote = None
            for row in report.legs:
                bar = next(
                    (
                        item
                        for item in row.source_bars
                        if item.observed_at == report.liquidation.triggered_at
                    ),
                    None,
                )
                if bar is None:
                    raise ValueError("Native liquidation time has no retained synchronized bar")
                bid, ask = _synthetic_open_quote(bar, row.quote_currency, row.slippage_bps)
                position = sum(
                    (
                        fill.quantity if fill.side == "BUY" else -fill.quantity
                        for fill in row.fills
                        if fill.observed_at < report.liquidation.triggered_at
                    ),
                    Decimal(0),
                )
                if position:
                    positions_at_trigger[row.instrument_id] = position
                prior_fills = tuple(
                    fill for fill in row.fills if fill.observed_at < report.liquidation.triggered_at
                )
                maintenance_legs.append(
                    (
                        row.instrument_id,
                        row.quote_currency,
                        position,
                        prior_fills,
                        bid,
                        ask,
                        Decimal(1),
                    )
                )
                if row.instrument_id == "USDJPY":
                    usd_jpy_quote = (bid, ask)
            equity, required_margin, instruments = _maintenance_metrics(
                report.initial_balance_usd, tuple(maintenance_legs), usd_jpy_quote
            )
            liquidation_fills = {
                row.instrument_id: row.fills[-1] for row in report.legs if row.fills
            }
            if (
                instruments != report.liquidation.instruments
                or equity != report.liquidation.equity_usd
                or required_margin != report.liquidation.required_margin_usd
                or tuple(sorted(positions_at_trigger)) != report.liquidation.instruments
                or any(row.final_position != 0 for row in report.legs)
                or any(
                    fill.observed_at > report.liquidation.triggered_at
                    for row in report.legs
                    for fill in row.fills
                )
                or any(
                    row.instrument_id not in liquidation_fills
                    or liquidation_fills[row.instrument_id].observed_at
                    != report.liquidation.triggered_at
                    or liquidation_fills[row.instrument_id].side
                    != ("SELL" if positions_at_trigger[row.instrument_id] > 0 else "BUY")
                    or liquidation_fills[row.instrument_id].quantity
                    != abs(positions_at_trigger[row.instrument_id])
                    for row in report.legs
                    if row.instrument_id in report.liquidation.instruments
                )
            ):
                raise ValueError("Native maintenance liquidation does not reconcile")


def _collateral_admitted(engine, prepared, active, order, fee):
    """Nautilus 1.221 margin risk bypasses its balance check; enforce a shared bound."""
    with localcontext(Context(prec=28, rounding=ROUND_CEILING)):
        return _collateral_bound(engine, prepared, active, order, fee)


def _collateral_bound(engine, prepared, active, order, fee):
    from nautilus_trader.model.enums import OrderSide

    current, proposed = Decimal(0), Decimal(0)
    for _, _, metadata, instrument, bridge in prepared:
        quote = engine.cache.quote_tick(instrument.id)
        if quote is None:
            return False
        unit = quote.ask_price.as_decimal()
        if metadata.quote_currency == "JPY":
            fx = engine.cache.quote_tick(
                next(item.id for _, _, info, item, _ in prepared if info.instrument_id == "USDJPY")
            )
            if fx is None:
                return False
            unit /= fx.bid_price.as_decimal()
        target = bridge.position
        if bridge is active:
            target += order.quantity.as_decimal() * (1 if order.side == OrderSide.BUY else -1)
        current += abs(bridge.position) * unit
        proposed += abs(target) * unit
    balance = engine.cache.accounts()[0].balance_total(Currency.from_str("USD")).as_decimal()
    return proposed < current or proposed + fee <= balance
