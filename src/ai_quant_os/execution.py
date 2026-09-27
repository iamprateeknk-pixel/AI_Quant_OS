import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from uuid import uuid4

from ai_quant_os.broker import BrokerRouter, BrokerUnavailable, SimulatedBroker
from ai_quant_os.contracts import OrderCommand, RiskDecision, TradeIntent, _require_aware
from ai_quant_os.order_store import PaperOrderStore
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.risk import RiskKernel, SafetyState


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    intent_id: str
    risk_id: str
    status: str
    command: OrderCommand


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
    )


def _with_fill_status(order: PaperOrder, fills: tuple[PaperFill, ...]) -> PaperOrder:
    quantity = sum((fill.quantity for fill in fills if fill.order_id == order.order_id), Decimal(0))
    if quantity == 0:
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
    ) -> None:
        self._risk = risk
        self._store = PaperOrderStore(store_path)
        self._router = router or BrokerRouter(
            [SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)]
        )
        self._lock = Lock()

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

    def replay(self, intent: TradeIntent, *, idempotency_key: str) -> PaperOrder | None:
        with self._lock:
            prior = self._store.get(idempotency_key)
            if prior is None:
                return None
            if prior[0] != _encode_record(intent):
                raise ValueError("idempotency key reused for a different intent")
            return _with_fill_status(_decode_order(prior[1]), self._store.all_fills())

    def _route(self, command: OrderCommand, *, now: datetime) -> None:
        acknowledgement = self._router.route(command, now=now)
        if acknowledgement.status != "ACCEPTED":
            raise BrokerUnavailable("Paper broker rejected order")

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
                raise ValueError("intent already has a paper order")
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
                order = PaperOrder(
                    order_id, intent.intent_id, decision.risk_id, "ACCEPTED", command
                )
                if not self._store.insert(
                    idempotency_key,
                    intent.intent_id,
                    intent_json,
                    _encode_record(order),
                    order_id,
                    lambda: self._route(command, now=now),
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
                return order

    def reconcile(
        self,
        *,
        local_positions: dict[str, Decimal],
        broker_positions: dict[str, Decimal],
    ) -> None:
        if local_positions != broker_positions:
            self._risk.set_safety_state(SafetyState.NO_NEW_RISK)
