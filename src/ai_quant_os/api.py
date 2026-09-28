import hmac
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.contracts import TradeIntent
from ai_quant_os.broker import BrokerRouter, SimulatedBroker
from ai_quant_os.execution import PaperExecutionGateway, PaperOrder
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.portfolio import PaperFill, summarize_position
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    world_state_version: str


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    desired_exposure: Decimal
    risk_budget: Decimal
    max_spread: Decimal
    max_slippage: Decimal
    valid_until: datetime
    world_state_version: str
    evidence_refs: tuple[str, ...]
    created_at: datetime


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class FillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fill_id: str
    order_id: str
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee: Decimal = Field(ge=0)
    observed_at: datetime


def _order_view(order: PaperOrder) -> dict[str, str]:
    view = {
        "order_id": order.order_id,
        "intent_id": order.intent_id,
        "risk_id": order.risk_id,
        "status": order.status,
        "broker_route": "PAPER",
    }
    if order.broker_order_id is not None:
        view["broker_order_id"] = order.broker_order_id
    return view


def _fill_view(fill: PaperFill) -> dict[str, str]:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "instrument_id": fill.instrument_id,
        "side": fill.side,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee": str(fill.fee),
        "observed_at": fill.observed_at.isoformat(),
        "source": "PAPER_MANUAL",
    }


def create_app(
    *,
    operator_token: str | None = None,
    clock: Callable[[], datetime] | None = None,
    paper_db_path: Path | None = None,
) -> FastAPI:
    configured_token = (
        operator_token if operator_token is not None else os.getenv("AI_QUANT_OPERATOR_TOKEN")
    )
    current_time = clock or (lambda: datetime.now(timezone.utc))
    limits = RiskLimits(
        max_order_exposure=Decimal("1000"),
        max_portfolio_exposure=Decimal("1500"),
        max_quote_age_seconds=5,
    )
    risk = RiskKernel(limits)
    configured_path = paper_db_path or (
        Path(os.environ["AI_QUANT_PAPER_DB"]) if os.getenv("AI_QUANT_PAPER_DB") else None
    )
    state_store = PaperStateStore(configured_path)
    risk.set_safety_state(state_store.safety_state())
    router = BrokerRouter([SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)])
    execution = PaperExecutionGateway(
        risk,
        store_path=configured_path,
        router=router,
    )
    quotes: dict[str, MarketSnapshot] = {}
    control_lock = Lock()
    app = FastAPI(title="AI Quant OS Paper Control API", version="0.1.0")

    for order in execution.orders:
        state_store.record_event(
            f"order:{order.order_id}",
            {"action": "PAPER_ORDER", "intent_id": order.intent_id, "reason": "APPROVE"},
        )
    for fill in execution.fills:
        state_store.record_event(
            f"fill:{fill.fill_id}",
            {
                "action": "PAPER_FILL",
                "intent_id": "",
                "reason": "SIMULATED",
                "trace_id": fill.fill_id,
            },
        )

    def require_operator(x_operator_token: str | None = Header(default=None)) -> None:
        if not configured_token:
            raise HTTPException(status_code=503, detail="Operator commands are disabled")
        if not x_operator_token or not hmac.compare_digest(x_operator_token, configured_token):
            raise HTTPException(status_code=401, detail="Invalid operator token")

    @app.get("/api/v1/system/status")
    def status() -> dict[str, str | int]:
        return {
            "mode": "PAPER",
            "safety_state": risk.safety_state.value,
            "order_count": len(execution.orders),
        }

    @app.get("/api/v1/risk")
    def risk_state() -> dict[str, str]:
        exposure = execution.exposure_snapshot
        return {
            "safety_state": risk.safety_state.value,
            "max_order_exposure": str(limits.max_order_exposure),
            "max_portfolio_exposure": str(limits.max_portfolio_exposure),
            "worst_case_quantity": str(exposure.worst_case_quantity),
            "filled_net_quantity": str(exposure.filled_net_quantity),
            "pending_buy_quantity": str(exposure.pending_buy_quantity),
            "pending_sell_quantity": str(exposure.pending_sell_quantity),
            "portfolio_snapshot_id": exposure.snapshot_id,
        }

    @app.get("/api/v1/brokers")
    def brokers() -> dict[str, list[dict[str, str | bool | list[str]]]]:
        return {"brokers": router.snapshot()}

    @app.get("/api/v1/markets/{instrument}")
    def market_detail(instrument: str) -> dict[str, str | bool]:
        snapshot = quotes.get(instrument)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No paper quote for instrument")
        age = (current_time() - snapshot.observed_at).total_seconds()
        freshness = (
            "FRESH" if snapshot.trusted and 0 <= age <= limits.max_quote_age_seconds else "STALE"
        )
        return {
            "instrument_id": snapshot.instrument_id,
            "bid": str(snapshot.bid),
            "ask": str(snapshot.ask),
            "observed_at": snapshot.observed_at.isoformat(),
            "world_state_version": snapshot.world_state_version,
            "trusted": snapshot.trusted,
            "freshness": freshness,
            "source": "PAPER_SIMULATED",
        }

    @app.get("/api/v1/orders")
    def orders() -> dict[str, list[dict[str, str]]]:
        return {"orders": [_order_view(order) for order in execution.orders]}

    @app.get("/api/v1/fills")
    def fills() -> dict[str, list[dict[str, str]]]:
        return {"fills": [_fill_view(fill) for fill in execution.fills]}

    @app.get("/api/v1/portfolio/snapshot")
    def portfolio_snapshot() -> dict[str, str | bool | None]:
        position_fills = execution.fills
        unmarked = summarize_position(position_fills, mark=None)
        quote = quotes.get("EURUSD")
        mark: Decimal | None = None
        if quote is not None and quote.trusted:
            age = (current_time() - quote.observed_at).total_seconds()
            if 0 <= age <= limits.max_quote_age_seconds:
                mark = quote.bid if unmarked.net_quantity >= 0 else quote.ask
        position = summarize_position(position_fills, mark=mark) if mark is not None else unmarked
        return {
            "mode": "PAPER",
            "instrument_id": "EURUSD",
            "quote_currency": "USD",
            "mark_available": mark is not None,
            "mark_price": str(mark) if mark is not None else None,
            "net_quantity": str(position.net_quantity),
            "cash_flow_quote": str(position.cash_flow_quote),
            "gross_exposure_quote": (
                str(position.gross_exposure_quote)
                if position.gross_exposure_quote is not None
                else None
            ),
            "net_exposure_quote": (
                str(position.net_exposure_quote)
                if position.net_exposure_quote is not None
                else None
            ),
            "marked_pnl_quote": (
                str(position.marked_pnl_quote) if position.marked_pnl_quote is not None else None
            ),
        }

    @app.get("/api/v1/audit")
    def audit_events() -> dict[str, list[dict[str, str]]]:
        return {"events": state_store.audit_events()}

    @app.post("/api/v1/paper/quotes", dependencies=[Depends(require_operator)])
    def record_quote(quote: QuoteInput) -> dict[str, str]:
        try:
            snapshot = MarketSnapshot(**quote.model_dump(), trusted=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        quotes[snapshot.instrument_id] = snapshot
        return {"instrument_id": snapshot.instrument_id, "mode": "PAPER"}

    @app.post("/api/v1/paper/intents", dependencies=[Depends(require_operator)])
    def submit_intent(
        payload: IntentInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str | None]:
        try:
            intent = TradeIntent(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            previous = execution.replay(intent, idempotency_key=idempotency_key)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if previous is not None:
            return _order_view(previous)
        quote = quotes.get(intent.instrument_id)
        if quote is None:
            raise HTTPException(status_code=409, detail="No paper market quote")
        now = current_time()
        decision = risk.evaluate(intent, quote, now=now, portfolio=execution.exposure_snapshot)
        try:
            order = execution.submit(intent, decision, idempotency_key=idempotency_key, now=now)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if order is None:
            state_store.record_event(
                f"risk:{decision.risk_id}",
                {
                    "action": "RISK_REJECT",
                    "intent_id": intent.intent_id,
                    "reason": ",".join(decision.violations),
                },
            )
            return {
                "order_id": None,
                "intent_id": intent.intent_id,
                "risk_id": decision.risk_id,
                "status": decision.decision,
                "broker_route": "PAPER",
            }
        state_store.record_event(
            f"order:{order.order_id}",
            {"action": "PAPER_ORDER", "intent_id": intent.intent_id, "reason": "APPROVE"},
        )
        return _order_view(order)

    @app.post("/api/v1/paper/fills", dependencies=[Depends(require_operator)])
    def record_fill(payload: FillInput) -> dict[str, str]:
        try:
            fill = execution.record_fill(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"fill:{fill.fill_id}",
            {
                "action": "PAPER_FILL",
                "intent_id": "",
                "reason": "SIMULATED",
                "trace_id": fill.fill_id,
            },
        )
        return _fill_view(fill)

    def transition(state: SafetyState, command: ControlInput, key: str) -> dict[str, str]:
        reason = command.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Reason is required")
        with control_lock:
            previous = state_store.control_receipt(key)
            if previous is not None:
                if previous[0] != state or previous[1] != reason:
                    raise HTTPException(status_code=409, detail="Idempotency key reused")
                return previous[2]
            receipt = {
                "receipt_id": str(uuid4()),
                "status": "COMPLETED",
                "safety_state": state.value,
                "reason": reason,
            }
            try:
                risk.set_safety_state(
                    state,
                    before_change=lambda: state_store.record_control(key, state, reason, receipt),
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            return receipt

    @app.post("/api/v1/control/no-new-risk", dependencies=[Depends(require_operator)])
    def no_new_risk(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        return transition(SafetyState.NO_NEW_RISK, command, idempotency_key)

    @app.post("/api/v1/control/flatten-only", dependencies=[Depends(require_operator)])
    def flatten_only(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        return transition(SafetyState.FLATTEN_ONLY, command, idempotency_key)

    @app.post("/api/v1/control/halt", dependencies=[Depends(require_operator)])
    def halt(command: ControlInput, idempotency_key: str = Header(min_length=1)) -> dict[str, str]:
        return transition(SafetyState.HALT, command, idempotency_key)

    return app


app = create_app()
