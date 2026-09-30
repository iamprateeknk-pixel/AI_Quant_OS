import hmac
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.contracts import TradeIntent
from ai_quant_os.currency_portfolio import FxRate, summarize_currency_portfolio
from ai_quant_os.currency_risk import CurrencyRiskContext, CurrencyRiskPolicy
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
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
        "instrument_id": order.command.instrument_id,
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
        "source": fill.source,
    }


def create_app(
    *,
    operator_token: str | None = None,
    recovery_token: str | None = None,
    clock: Callable[[], datetime] | None = None,
    paper_db_path: Path | None = None,
) -> FastAPI:
    configured_token = (
        operator_token if operator_token is not None else os.getenv("AI_QUANT_OPERATOR_TOKEN")
    )
    configured_recovery_token = (
        recovery_token if recovery_token is not None else os.getenv("AI_QUANT_RECOVERY_TOKEN")
    )
    if (
        configured_token
        and configured_recovery_token
        and hmac.compare_digest(configured_token, configured_recovery_token)
    ):
        raise ValueError("Recovery token must differ from operator token")
    current_time = clock or (lambda: datetime.now(timezone.utc))
    limits = RiskLimits(
        max_order_exposure=Decimal("1000"),
        max_portfolio_exposure=Decimal("1500"),
        max_quote_age_seconds=5,
    )
    currency_policy = CurrencyRiskPolicy(
        "USD", Decimal("2000"), Decimal("4000"), 5, PAPER_INSTRUMENTS
    )
    instrument_catalog = {item.instrument_id: item for item in PAPER_INSTRUMENTS}
    risk = RiskKernel(limits, currency_policy=currency_policy)
    configured_path = paper_db_path or (
        Path(os.environ["AI_QUANT_PAPER_DB"]) if os.getenv("AI_QUANT_PAPER_DB") else None
    )
    state_store = PaperStateStore(configured_path)
    risk.set_safety_state(state_store.safety_state())
    router = BrokerRouter(
        [
            SimulatedBroker(
                "PAPER",
                {identifier: identifier for identifier in instrument_catalog},
                enabled=True,
                store_path=configured_path,
            )
        ]
    )
    execution = PaperExecutionGateway(
        risk,
        store_path=configured_path,
        router=router,
        on_uncertain_order=state_store.record_order_uncertainty,
        clock=current_time,
    )
    quotes: dict[str, MarketSnapshot] = {}
    control_lock = Lock()
    app = FastAPI(title="AI Quant OS Paper Control API", version="0.1.0")

    def currency_context(primary_quote: MarketSnapshot | None = None) -> CurrencyRiskContext:
        instruments = currency_policy.instruments
        rates: list[FxRate] = []
        for item in instruments:
            quote = (
                primary_quote
                if primary_quote is not None and primary_quote.instrument_id == item.instrument_id
                else quotes.get(item.instrument_id)
            )
            if quote is not None:
                rates.append(
                    FxRate(
                        item.base_currency,
                        item.quote_currency,
                        quote.bid,
                        quote.ask,
                        quote.observed_at,
                        quote.trusted,
                    )
                )
        ledger_fills, pending = execution.currency_ledger
        return CurrencyRiskContext(ledger_fills, pending, instruments, tuple(rates))

    for order in execution.orders:
        state_store.backfill_event(
            f"order:{order.order_id}",
            {"action": "PAPER_ORDER", "intent_id": order.intent_id, "reason": "APPROVE"},
        )
    for fill in execution.fills:
        state_store.backfill_event(
            f"fill:{fill.fill_id}",
            {
                "action": "PAPER_FILL",
                "intent_id": "",
                "reason": "PAPER_BROKER" if fill.source == "PAPER_BROKER" else "SIMULATED",
                "trace_id": fill.fill_id,
            },
        )

    def require_operator(x_operator_token: str | None = Header(default=None)) -> None:
        if not configured_token:
            raise HTTPException(status_code=503, detail="Operator commands are disabled")
        if not x_operator_token or not hmac.compare_digest(x_operator_token, configured_token):
            raise HTTPException(status_code=401, detail="Invalid operator token")
        if not state_store.audit_integrity().valid:
            raise HTTPException(status_code=503, detail="Audit integrity verification failed")

    def require_recovery(x_recovery_token: str | None = Header(default=None)) -> None:
        if not configured_recovery_token:
            raise HTTPException(status_code=503, detail="Paper recovery is disabled")
        if not x_recovery_token or not hmac.compare_digest(
            x_recovery_token, configured_recovery_token
        ):
            raise HTTPException(status_code=401, detail="Invalid recovery token")

    @app.get("/api/v1/system/status")
    def status() -> dict[str, str | int]:
        return {
            "mode": "PAPER",
            "safety_state": risk.safety_state.value,
            "order_count": len(execution.orders),
        }

    @app.get("/api/v1/risk")
    def risk_state(instrument: str = "EURUSD") -> dict[str, str]:
        if instrument not in instrument_catalog:
            raise HTTPException(status_code=404, detail="Unknown paper instrument")
        exposure = execution.exposure_for(instrument)
        return {
            "instrument_id": instrument,
            "safety_state": risk.safety_state.value,
            "max_order_exposure": str(limits.max_order_exposure),
            "max_portfolio_exposure": str(limits.max_portfolio_exposure),
            "worst_case_quantity": str(exposure.worst_case_quantity),
            "filled_net_quantity": str(exposure.filled_net_quantity),
            "pending_buy_quantity": str(exposure.pending_buy_quantity),
            "pending_sell_quantity": str(exposure.pending_sell_quantity),
            "portfolio_snapshot_id": exposure.snapshot_id,
            "reporting_currency": currency_policy.reporting_currency,
            "max_currency_exposure_reporting": str(currency_policy.max_currency_exposure_reporting),
            "max_gross_currency_exposure_reporting": str(
                currency_policy.max_gross_exposure_reporting
            ),
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

    def position_view(
        instrument: str, fills: tuple[PaperFill, ...], quote: MarketSnapshot | None, now: datetime
    ) -> dict[str, str | bool | None]:
        position_fills = tuple(fill for fill in fills if fill.instrument_id == instrument)
        unmarked = summarize_position(position_fills, mark=None)
        mark: Decimal | None = None
        if quote is not None and quote.trusted:
            age = (now - quote.observed_at).total_seconds()
            if 0 <= age <= limits.max_quote_age_seconds:
                mark = quote.bid if unmarked.net_quantity >= 0 else quote.ask
        position = summarize_position(position_fills, mark=mark) if mark is not None else unmarked
        return {
            "mode": "PAPER",
            "instrument_id": instrument,
            "base_currency": instrument_catalog[instrument].base_currency,
            "quote_currency": instrument_catalog[instrument].quote_currency,
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

    @app.get("/api/v1/portfolio/snapshot")
    def portfolio_snapshot(instrument: str = "EURUSD") -> dict[str, str | bool | None]:
        if instrument not in instrument_catalog:
            raise HTTPException(status_code=404, detail="Unknown paper instrument")
        return position_view(instrument, execution.fills, quotes.get(instrument), current_time())

    @app.get("/api/v1/portfolio/positions")
    def portfolio_positions() -> dict[str, object]:
        fills = execution.fills
        observed_quotes = quotes.copy()
        now = current_time()
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "positions": [
                position_view(
                    item.instrument_id, fills, observed_quotes.get(item.instrument_id), now
                )
                for item in PAPER_INSTRUMENTS
            ],
        }

    @app.get("/api/v1/instruments")
    def instruments() -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "instruments": jsonable_encoder(PAPER_INSTRUMENTS),
        }

    @app.get("/api/v1/audit/integrity")
    def audit_integrity() -> dict[str, object]:
        return {"schema_version": "1.0", **jsonable_encoder(state_store.audit_integrity())}

    @app.get("/api/v1/audit")
    def audit_events() -> dict[str, list[dict[str, str]]]:
        try:
            return {"events": state_store.audit_events()}
        except ValueError as error:
            raise HTTPException(
                status_code=503, detail="Audit integrity verification failed"
            ) from error

    @app.get("/api/v1/portfolio/currencies")
    def currency_portfolio() -> dict[str, object]:
        try:
            context = currency_context()
            snapshot = summarize_currency_portfolio(
                context.fills,
                pending=context.pending,
                instruments=context.instruments,
                rates=context.rates,
                reporting_currency=currency_policy.reporting_currency,
                now=current_time(),
                max_rate_age_seconds=currency_policy.max_rate_age_seconds,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            **jsonable_encoder(snapshot, custom_encoder={Decimal: str}),
            "valuation_available": snapshot.valuation_available,
            "mode": "PAPER",
        }

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
            if previous.status == "PENDING":
                quote = quotes.get(intent.instrument_id)
                if quote is None:
                    raise HTTPException(status_code=409, detail="No paper market quote")
                try:
                    previous = execution.recover_pending(
                        intent,
                        idempotency_key=idempotency_key,
                        market=quote,
                        now=current_time(),
                        currency_context=currency_context(quote),
                    )
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                if previous.status == "ACCEPTED":
                    state_store.record_event(
                        f"order:{previous.order_id}",
                        {
                            "action": "PAPER_ORDER",
                            "intent_id": intent.intent_id,
                            "reason": "APPROVE",
                        },
                    )
            return _order_view(previous)
        quote = quotes.get(intent.instrument_id)
        if quote is None:
            raise HTTPException(status_code=409, detail="No paper market quote")
        now = current_time()
        try:
            decision = risk.evaluate(
                intent,
                quote,
                now=now,
                portfolio=execution.exposure_for(intent.instrument_id),
                currency_context=currency_context(quote),
            )
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

    @app.post("/api/v1/paper/broker-fills", dependencies=[Depends(require_operator)])
    def simulate_broker_fill(payload: FillInput) -> dict[str, str]:
        try:
            fill = execution.simulate_broker_fill(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"broker-fill:{fill.fill_id}",
            {
                "action": "PAPER_BROKER_FILL_RECORDED",
                "intent_id": "",
                "reason": "AWAITING_SYNC",
                "trace_id": fill.fill_id,
            },
        )
        return _fill_view(fill)

    @app.post(
        "/api/v1/paper/orders/{order_id}/sync-fills", dependencies=[Depends(require_operator)]
    )
    def sync_broker_fills(order_id: str) -> dict[str, str | int]:
        try:
            imported = execution.sync_broker_fills(order_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        for fill in execution.fills:
            if fill.order_id == order_id and fill.source == "PAPER_BROKER":
                state_store.record_event(
                    f"fill:{fill.fill_id}",
                    {
                        "action": "PAPER_FILL",
                        "intent_id": "",
                        "reason": "PAPER_BROKER",
                        "trace_id": fill.fill_id,
                    },
                )
        return {"order_id": order_id, "imported_count": imported}

    @app.post("/api/v1/paper/positions/reconcile", dependencies=[Depends(require_operator)])
    def reconcile_paper_positions() -> dict[str, object]:
        try:
            result = execution.compare_paper_positions()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"position-reconciliation:{uuid4()}",
            {
                "action": "PAPER_POSITION_RECONCILIATION",
                "intent_id": "",
                "reason": "MATCH" if result.matches else "MISMATCH",
                "local_net_quantity": str(result.local_net_quantity),
                "broker_net_quantity": str(result.broker_net_quantity),
                "local_cash_flow_quote": str(result.local_cash_flow_quote),
                "broker_cash_flow_quote": str(result.broker_cash_flow_quote),
                "local_only_fill_ids": ",".join(result.local_only_fill_ids),
                "broker_only_fill_ids": ",".join(result.broker_only_fill_ids),
                "conflicting_fill_ids": ",".join(result.conflicting_fill_ids),
                "mismatched_instruments": ",".join(
                    item.instrument_id for item in result.instruments if not item.matches
                ),
            },
        )
        return {
            "schema_version": "1.1",
            "summary_instrument_id": "EURUSD",
            "instruments": jsonable_encoder(result.instruments, custom_encoder={Decimal: str}),
            "matches": result.matches,
            "local_net_quantity": str(result.local_net_quantity),
            "broker_net_quantity": str(result.broker_net_quantity),
            "local_cash_flow_quote": str(result.local_cash_flow_quote),
            "broker_cash_flow_quote": str(result.broker_cash_flow_quote),
            "local_only_fill_ids": list(result.local_only_fill_ids),
            "broker_only_fill_ids": list(result.broker_only_fill_ids),
            "conflicting_fill_ids": list(result.conflicting_fill_ids),
        }

    @app.post("/api/v1/paper/orders/{order_id}/reconcile", dependencies=[Depends(require_operator)])
    def reconcile_paper_order(order_id: str) -> dict[str, str]:
        try:
            order = execution.reconcile_order(order_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if order.status in {"ACCEPTED", "REJECTED"}:
            state_store.record_event(
                f"reconcile:{order.order_id}",
                {
                    "action": "PAPER_ORDER_RECONCILED",
                    "intent_id": order.intent_id,
                    "reason": order.status,
                },
            )
        return _order_view(order)

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

    @app.post(
        "/api/v1/control/recover-paper",
        dependencies=[Depends(require_operator), Depends(require_recovery)],
    )
    def recover_paper(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        reason = command.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Reason is required")
        with control_lock:
            previous = state_store.recovery_receipt(idempotency_key)
            if previous is not None:
                if previous[0] != reason:
                    raise HTTPException(status_code=409, detail="Idempotency key reused")
                return previous[1]
            receipt = {
                "receipt_id": str(uuid4()),
                "status": "COMPLETED",
                "safety_state": SafetyState.NORMAL.value,
                "reason": reason,
            }
            try:
                execution.recover_paper_safety(
                    before_change=lambda: state_store.record_recovery(
                        idempotency_key, reason, receipt
                    )
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            return receipt

    return app


app = create_app()
