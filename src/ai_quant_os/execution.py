import json
from hashlib import sha256
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from ai_quant_os.broker import (
    BrokerOutcomeUnknown,
    BrokerRouter,
    BrokerUnavailable,
    SimulatedBroker,
)
from ai_quant_os.contracts import OrderCommand, RiskDecision, TradeIntent, _require_aware
from ai_quant_os.currency_portfolio import CurrencyPendingOrder
from ai_quant_os.currency_risk import CurrencyRiskContext
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.order_store import PaperOrderStore
from ai_quant_os.portfolio import PaperFill, PortfolioExposure, summarize_position
from ai_quant_os.risk import RiskKernel, SafetyState
from ai_quant_os.paper_attribution import OrderAttribution


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    intent_id: str
    risk_id: str
    status: str
    command: OrderCommand
    broker_order_id: str | None = None


@dataclass(frozen=True)
class InstrumentPositionReconciliation:
    instrument_id: str
    matches: bool
    local_net_quantity: Decimal
    broker_net_quantity: Decimal
    local_cash_flow_quote: Decimal
    broker_cash_flow_quote: Decimal


@dataclass(frozen=True)
class PositionReconciliation:
    matches: bool
    local_net_quantity: Decimal
    broker_net_quantity: Decimal
    local_cash_flow_quote: Decimal
    broker_cash_flow_quote: Decimal
    local_only_fill_ids: tuple[str, ...]
    broker_only_fill_ids: tuple[str, ...]
    conflicting_fill_ids: tuple[str, ...]
    instruments: tuple[InstrumentPositionReconciliation, ...] = ()


def _encode_value(value: Decimal | datetime) -> str:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported order value: {type(value)}")


def _encode_record(record: TradeIntent | PaperOrder) -> str:
    return json.dumps(asdict(record), default=_encode_value, sort_keys=True)


def _decode_order(raw: str) -> PaperOrder:
    data = json.loads(raw)
    command = data["command"]
    for field in ("quantity", "limit_price", "stop_price"):
        if command[field] is not None:
            command[field] = Decimal(command[field])
    command["expires_at"] = datetime.fromisoformat(command["expires_at"])
    return PaperOrder(
        order_id=data["order_id"],
        intent_id=data["intent_id"],
        risk_id=data["risk_id"],
        status=data["status"],
        command=OrderCommand(**command),
        broker_order_id=data.get("broker_order_id"),
    )


def _with_fill_status(order: PaperOrder, fills: tuple[PaperFill, ...]) -> PaperOrder:
    quantity = sum((fill.quantity for fill in fills if fill.order_id == order.order_id), Decimal(0))
    if quantity == 0 or order.status != "ACCEPTED":
        return order
    status = "FILLED" if quantity == order.command.quantity else "PARTIALLY_FILLED"
    return replace(order, status=status)


class PaperExecutionGateway:
    def __init__(
        self,
        risk: RiskKernel,
        *,
        store_path: Path | None = None,
        router: BrokerRouter | None = None,
        on_uncertain_order: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._risk = risk
        self._store = PaperOrderStore(store_path)
        self._router = router or BrokerRouter(
            [SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)]
        )
        self._lock = RLock()
        self._on_uncertain_order = on_uncertain_order
        self._clock = clock
        for order in (_decode_order(raw) for raw in self._store.all_orders()):
            if order.status in {"DISPATCHING", "UNKNOWN"}:
                self._escalate_uncertainty(order.order_id)

    @property
    def orders(self) -> tuple[PaperOrder, ...]:
        with self._lock:
            fills = self._store.all_fills()
            return tuple(
                _with_fill_status(_decode_order(raw), fills) for raw in self._store.all_orders()
            )

    @property
    def fills(self) -> tuple[PaperFill, ...]:
        return self._store.all_fills()

    @property
    def currency_ledger(self) -> tuple[tuple[PaperFill, ...], tuple[CurrencyPendingOrder, ...]]:
        with self._lock:
            return self._store.currency_ledger()

    @property
    def attribution_ledger(self):
        with self._lock:
            rows, fills = self._store.attribution_records()
            owners = []
            for order_id, intent_id, intent_raw, order_raw in rows:
                order = _decode_order(order_raw)
                intent = json.loads(intent_raw)
                command = order.command
                if (
                    command.broker_route != "PAPER"
                    or command.order_id != order_id
                    or command.risk_id != order.risk_id
                    or order.order_id != order_id
                    or order.intent_id != intent_id
                    or intent["intent_id"] != intent_id
                    or (command.instrument_id, command.side)
                    != (intent["instrument_id"], intent["side"])
                    or command.quantity != Decimal(intent["desired_exposure"])
                    or command.expires_at != datetime.fromisoformat(intent["valid_until"])
                ):
                    raise ValueError("Paper attribution order ownership mismatch")
                owners.append(
                    OrderAttribution(
                        order_id,
                        intent_id,
                        intent["strategy_id"],
                        intent["strategy_version"],
                        command.instrument_id,
                        command.side,
                        command.quantity,
                        order.status,
                        datetime.fromisoformat(intent["created_at"]),
                        command.expires_at,
                        sha256(intent_raw.encode()).hexdigest(),
                        sha256(order_raw.encode()).hexdigest(),
                    )
                )
            return tuple(owners), fills

    @property
    def exposure_snapshot(self) -> PortfolioExposure:
        return self._store.exposure_snapshot()

    def exposure_for(self, instrument_id: str) -> PortfolioExposure:
        return self._store.exposure_snapshot(instrument_id)

    def record_fill(
        self,
        *,
        fill_id: str,
        order_id: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        observed_at: datetime,
    ) -> PaperFill:
        with self._lock:
            raw = self._store.get_order_by_id(order_id)
            if raw is None:
                raise ValueError("Paper order does not exist")
            command = _decode_order(raw).command
            fill = PaperFill(
                fill_id,
                order_id,
                command.instrument_id,
                command.side,
                quantity,
                price,
                fee,
                observed_at,
            )
            return self._store.record_fill(fill)

    def simulate_broker_fill(
        self,
        *,
        fill_id: str,
        order_id: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        observed_at: datetime,
    ) -> PaperFill:
        with self._lock:
            raw = self._store.get_order_by_id(order_id)
            if raw is None:
                raise ValueError("Paper order does not exist")
            order = _decode_order(raw)
            if order.status != "ACCEPTED":
                raise ValueError("Paper order is not accepted")
            fill = PaperFill(
                fill_id,
                order_id,
                order.command.instrument_id,
                order.command.side,
                quantity,
                price,
                fee,
                observed_at,
                "PAPER_BROKER",
            )
            return self._router.simulate_fill(order.command, fill)

    def sync_broker_fills(self, order_id: str) -> int:
        with self._lock:
            raw = self._store.get_order_by_id(order_id)
            if raw is None:
                raise ValueError("Paper order does not exist")
            order = _decode_order(raw)
            if order.status != "ACCEPTED":
                raise ValueError("Paper order is not accepted")
            try:
                broker_fills = self._router.fills(order.command)
                if any(
                    fill.order_id != order_id or fill.source != "PAPER_BROKER"
                    for fill in broker_fills
                ):
                    raise ValueError("Broker fill does not match requested order")
                _, imported = self._store.record_fills(broker_fills)
            except Exception:
                self._escalate_uncertainty(order_id)
                raise
            return imported

    def compare_paper_positions(self) -> PositionReconciliation:
        with self._lock:
            local_fills = self._store.all_fills()
            try:
                broker_fills = self._router.all_fills("PAPER")
                comparisons: list[InstrumentPositionReconciliation] = []
                identifiers = sorted({fill.instrument_id for fill in local_fills + broker_fills})
                for identifier in identifiers:
                    local = tuple(fill for fill in local_fills if fill.instrument_id == identifier)
                    broker = tuple(
                        fill for fill in broker_fills if fill.instrument_id == identifier
                    )
                    local_summary = summarize_position(local, mark=None)
                    broker_summary = summarize_position(broker, mark=None)
                    matches = (
                        local_summary.net_quantity == broker_summary.net_quantity
                        and local_summary.cash_flow_quote == broker_summary.cash_flow_quote
                        and {fill.fill_id: fill for fill in local}
                        == {fill.fill_id: fill for fill in broker}
                    )
                    comparisons.append(
                        InstrumentPositionReconciliation(
                            identifier,
                            matches,
                            local_summary.net_quantity,
                            broker_summary.net_quantity,
                            local_summary.cash_flow_quote,
                            broker_summary.cash_flow_quote,
                        )
                    )
                local_position = summarize_position(
                    tuple(fill for fill in local_fills if fill.instrument_id == "EURUSD"), mark=None
                )
                broker_position = summarize_position(
                    tuple(fill for fill in broker_fills if fill.instrument_id == "EURUSD"),
                    mark=None,
                )
                local_by_id = {fill.fill_id: fill for fill in local_fills}
                broker_by_id = {fill.fill_id: fill for fill in broker_fills}
                local_only = tuple(sorted(local_by_id.keys() - broker_by_id.keys()))
                broker_only = tuple(sorted(broker_by_id.keys() - local_by_id.keys()))
                conflicting = tuple(
                    sorted(
                        fill_id
                        for fill_id in local_by_id.keys() & broker_by_id.keys()
                        if local_by_id[fill_id] != broker_by_id[fill_id]
                    )
                )
                matches = (
                    all(item.matches for item in comparisons)
                    and not local_only
                    and not broker_only
                    and not conflicting
                )
            except Exception:
                self._escalate_uncertainty("position:PAPER")
                raise
            if not matches:
                self._escalate_uncertainty("position:PAPER")
            return PositionReconciliation(
                matches,
                local_position.net_quantity,
                broker_position.net_quantity,
                local_position.cash_flow_quote,
                broker_position.cash_flow_quote,
                local_only,
                broker_only,
                conflicting,
                tuple(comparisons),
            )

    def recover_paper_safety(self, *, before_change: Callable[[], None]) -> None:
        with self._lock:
            if any(
                _decode_order(raw).status in {"PENDING", "DISPATCHING", "UNKNOWN"}
                for raw in self._store.all_orders()
            ):
                raise ValueError("Unresolved paper orders block safety recovery")
            if not self.compare_paper_positions().matches:
                raise ValueError("Paper fill ledger mismatch blocks safety recovery")
            self._risk.recover_paper_safety(before_change=before_change)

    def replay(self, intent: TradeIntent, *, idempotency_key: str) -> PaperOrder | None:
        with self._lock:
            prior = self._store.get(idempotency_key)
            if prior is None:
                return None
            if prior[0] != _encode_record(intent):
                raise ValueError("idempotency key reused for a different intent")
            return _with_fill_status(_decode_order(prior[1]), self._store.all_fills())

    def _mark_unknown(self, order_id: str) -> None:
        try:
            self._store.transition(order_id, from_status="DISPATCHING", to_status="UNKNOWN")
        finally:
            self._escalate_uncertainty(order_id)

    def _escalate_uncertainty(self, order_id: str) -> None:
        if self._risk.safety_state != SafetyState.NORMAL:
            if self._on_uncertain_order is not None:
                self._on_uncertain_order(order_id)
            return
        try:
            self._risk.set_safety_state(
                SafetyState.NO_NEW_RISK,
                before_change=(
                    (lambda: self._on_uncertain_order(order_id))
                    if self._on_uncertain_order is not None
                    else None
                ),
            )
        except Exception:
            self._risk.set_safety_state(SafetyState.NO_NEW_RISK)
            raise

    def _dispatch(self, order: PaperOrder, *, now: datetime) -> PaperOrder:
        order_id = order.order_id
        command = order.command
        try:
            acknowledgement = self._router.route(command, now=now)
        except BrokerOutcomeUnknown:
            self._mark_unknown(order_id)
            raise
        except BrokerUnavailable:
            self._store.transition(order_id, from_status="DISPATCHING", to_status="REJECTED")
            raise
        if acknowledgement.broker_id != command.broker_route:
            self._mark_unknown(order_id)
            raise BrokerOutcomeUnknown("Broker acknowledgement route does not match")
        if acknowledgement.status == "REJECTED":
            self._store.transition(order_id, from_status="DISPATCHING", to_status="REJECTED")
            raise BrokerUnavailable("Paper broker rejected order")
        if acknowledgement.status != "ACCEPTED" or not acknowledgement.broker_order_id:
            self._mark_unknown(order_id)
            raise BrokerOutcomeUnknown("Broker acknowledgement is ambiguous")
        try:
            self._store.transition(
                order_id,
                from_status="DISPATCHING",
                to_status="ACCEPTED",
                broker_order_id=acknowledgement.broker_order_id,
            )
        except Exception:
            self._escalate_uncertainty(order_id)
            raise
        return replace(order, status="ACCEPTED", broker_order_id=acknowledgement.broker_order_id)

    def recover_pending(
        self,
        intent: TradeIntent,
        *,
        idempotency_key: str,
        market: MarketSnapshot,
        now: datetime,
        currency_context: CurrencyRiskContext | None = None,
    ) -> PaperOrder:
        _require_aware(now)
        with self._lock:
            prior = self._store.get(idempotency_key)
            if prior is None:
                raise ValueError("Paper order does not exist")
            if prior[0] != _encode_record(intent):
                raise ValueError("idempotency key reused for a different intent")
            order = _decode_order(prior[1])
            if order.status != "PENDING":
                return _with_fill_status(order, self._store.all_fills())
            if now >= intent.valid_until:
                self._store.transition(order.order_id, from_status="PENDING", to_status="REJECTED")
                return replace(order, status="REJECTED")
            if self._risk.safety_state != SafetyState.NORMAL:
                raise BrokerUnavailable("Risk safety state blocks pending order recovery")
            portfolio = self._store.exposure_snapshot(intent.instrument_id)
            command = order.command
            if command.side == "BUY":
                adjusted = replace(
                    portfolio,
                    snapshot_id=f"recovery:{portfolio.snapshot_id}",
                    pending_buy_quantity=portfolio.pending_buy_quantity - command.quantity,
                )
            else:
                adjusted = replace(
                    portfolio,
                    snapshot_id=f"recovery:{portfolio.snapshot_id}",
                    pending_sell_quantity=portfolio.pending_sell_quantity - command.quantity,
                )
            if currency_context is not None:
                currency_context = replace(
                    currency_context,
                    pending=tuple(
                        item for item in currency_context.pending if item.order_id != order.order_id
                    ),
                )
            decision = self._risk.evaluate(
                intent, market, now=now, portfolio=adjusted, currency_context=currency_context
            )
            with self._risk.authorization(intent, decision) as authorized:
                if not authorized:
                    raise BrokerUnavailable("Current market or risk checks block pending recovery")
                try:
                    dispatch_now = self._clock() if self._clock is not None else now
                    self._store.transition(
                        order.order_id,
                        from_status="PENDING",
                        to_status="DISPATCHING",
                        currency_reservation=self._risk.currency_reservation(decision),
                        now=dispatch_now,
                    )
                except ValueError:
                    current = self._store.get(idempotency_key)
                    current_order = _decode_order(current[1])
                    if current_order.status == "PENDING":
                        raise
                    return _with_fill_status(current_order, self._store.all_fills())
                self._risk.consume(decision.risk_id)
                return self._dispatch(order, now=dispatch_now)

    def reconcile_order(self, order_id: str) -> PaperOrder:
        with self._lock:
            raw = self._store.get_order_by_id(order_id)
            if raw is None:
                raise ValueError("Paper order does not exist")
            order = _decode_order(raw)
            if order.status not in {"DISPATCHING", "UNKNOWN"}:
                return _with_fill_status(order, self._store.all_fills())
            self._escalate_uncertainty(order_id)
            acknowledgement = self._router.lookup(order.command)
            if acknowledgement is None:
                return order
            if acknowledgement.broker_id != order.command.broker_route:
                raise BrokerOutcomeUnknown("Broker acknowledgement route does not match")
            if acknowledgement.status == "REJECTED":
                self._store.transition(order_id, from_status=order.status, to_status="REJECTED")
                return replace(order, status="REJECTED")
            if acknowledgement.status != "ACCEPTED" or not acknowledgement.broker_order_id:
                raise BrokerOutcomeUnknown("Broker acknowledgement is ambiguous")
            self._store.transition(
                order_id,
                from_status=order.status,
                to_status="ACCEPTED",
                broker_order_id=acknowledgement.broker_order_id,
            )
            return replace(
                order, status="ACCEPTED", broker_order_id=acknowledgement.broker_order_id
            )

    def submit(
        self,
        intent: TradeIntent,
        decision: RiskDecision,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> PaperOrder | None:
        _require_aware(now)
        if not idempotency_key:
            raise ValueError("Idempotency key is required")
        intent_json = _encode_record(intent)
        with self._lock:
            prior = self._store.get(idempotency_key)
            if prior is not None:
                if prior[0] != intent_json:
                    raise ValueError("idempotency key reused for a different intent")
                return _with_fill_status(_decode_order(prior[1]), self._store.all_fills())
            if self._store.get_by_intent_id(intent.intent_id) is not None:
                prior = self._store.get(idempotency_key)
                if prior is None:
                    raise ValueError("intent already has a paper order")
                if prior[0] != intent_json:
                    raise ValueError("idempotency key reused for a different intent")
                return _with_fill_status(_decode_order(prior[1]), self._store.all_fills())
            with self._risk.authorization(intent, decision) as authorized:
                if not authorized:
                    return None
                if (
                    decision.intent_id != intent.intent_id
                    or decision.approved_exposure != intent.desired_exposure
                ):
                    raise ValueError("Risk decision does not authorize this intent")
                if now >= intent.valid_until or decision.created_at > now:
                    return None
                order_id = str(uuid4())
                command = OrderCommand(
                    order_id=order_id,
                    idempotency_key=idempotency_key,
                    risk_id=decision.risk_id,
                    broker_route="PAPER",
                    instrument_id=intent.instrument_id,
                    order_type="MARKET",
                    side=intent.side,
                    quantity=decision.approved_exposure,
                    limit_price=None,
                    stop_price=None,
                    time_in_force="IOC",
                    expires_at=intent.valid_until,
                )
                order = PaperOrder(order_id, intent.intent_id, decision.risk_id, "PENDING", command)
                if not self._store.insert(
                    idempotency_key,
                    intent.intent_id,
                    intent_json,
                    _encode_record(order),
                    order_id,
                    expected_snapshot_id=decision.portfolio_snapshot_id,
                    max_portfolio_exposure=self._risk.max_portfolio_exposure,
                    currency_reservation=self._risk.currency_reservation(decision),
                    now=self._clock() if self._clock is not None else now,
                ):
                    prior = self._store.get(idempotency_key)
                    if prior is None:
                        if self._store.get_by_intent_id(intent.intent_id) is not None:
                            raise ValueError("intent already has a paper order")
                        raise ValueError("Order receipt conflict")
                    if prior[0] != intent_json:
                        raise ValueError("idempotency key reused for a different intent")
                    return _with_fill_status(_decode_order(prior[1]), self._store.all_fills())
                self._risk.consume(decision.risk_id)
                dispatch_now = self._clock() if self._clock is not None else now
                self._store.transition(
                    order_id,
                    from_status="PENDING",
                    to_status="DISPATCHING",
                    currency_reservation=self._risk.currency_reservation(decision),
                    now=dispatch_now,
                )
                return self._dispatch(order, now=dispatch_now)

    def reconcile(
        self,
        *,
        local_positions: dict[str, Decimal],
        broker_positions: dict[str, Decimal],
    ) -> None:
        if local_positions != broker_positions:
            self._risk.set_safety_state(SafetyState.NO_NEW_RISK)
