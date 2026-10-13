"""Audited operator control for PAPER new-order route admission."""

from datetime import timedelta
from hashlib import sha256
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, UUID4, model_validator

from ai_quant_os.broker import BrokerRouter
from ai_quant_os.operator_session_contracts import OperatorSession
from ai_quant_os.paper_state_store import OperatorReservationConflict, PaperStateStore
from ai_quant_os.risk import RiskKernel, SafetyState


class BrokerRouteControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID4
    requested_enabled: bool = Field(strict=True)
    expected_enabled: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=512)
    operator_session_id: UUID4
    client_timestamp: AwareDatetime
    confirmation: Literal["ENABLE", "DISABLE"]

    @model_validator(mode="after")
    def command_is_confirmed(self):
        expected_confirmation = "ENABLE" if self.requested_enabled else "DISABLE"
        if not self.reason.strip() or self.confirmation != expected_confirmation:
            raise ValueError("Reason and exact route confirmation are required")
        if self.client_timestamp.utcoffset() != timedelta(0):
            raise ValueError("Route command timestamp must be UTC")
        return self


class BrokerRouteControlReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    broker_id: Literal["PAPER"]
    action_id: UUID4
    decision: Literal["ACCEPTED", "REJECTED"]
    expected_enabled: bool = Field(strict=True)
    requested_enabled: bool = Field(strict=True)
    previous_enabled: bool = Field(strict=True)
    authoritative_enabled: bool = Field(strict=True)
    operator_session_id: UUID4
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_timestamp: AwareDatetime
    trace_id: UUID4
    policy_checks: tuple[
        Literal[
            "PAPER_ONLY",
            "VALID_SESSION",
            "FRESH_REAUTH",
            "EXPECTED_STATE",
            "EXPECTED_STATE_REJECTED",
            "COMMAND_FRESHNESS_REJECTED",
            "NEW_ORDERS_ONLY",
        ],
        ...,
    ]
    rejection_reason: (
        Literal[
            "STALE_STATE",
            "STALE_COMMAND",
            "BROKER_DISCONNECTED",
            "BROKER_NOT_ELIGIBLE",
            "SAFETY_STATE_BLOCKS_ENABLE",
        ]
        | None
    )

    @model_validator(mode="after")
    def receipt_reflects_authoritative_result(self):
        if self.server_timestamp.utcoffset() != timedelta(0):
            raise ValueError("Route receipt timestamp must be UTC")
        if self.decision == "ACCEPTED":
            if self.authoritative_enabled != self.requested_enabled or self.rejection_reason:
                raise ValueError("Accepted route receipt does not match requested state")
        elif self.authoritative_enabled != self.previous_enabled or self.rejection_reason is None:
            raise ValueError("Rejected route receipt does not preserve current state")
        return self


def install_broker_route_control_routes(
    app: FastAPI,
    authorization,
    store: PaperStateStore,
    router: BrokerRouter,
    risk: RiskKernel,
    control_lock,
    clock,
    *,
    persistent: bool,
):
    def unavailable_store():
        if not persistent:
            raise HTTPException(503, "Persistent broker route audit is unavailable")
        return store

    @app.get(
        "/api/v1/brokers/route-controls/{action_id}",
        dependencies=[Depends(authorization.require_session)],
        response_model=BrokerRouteControlReceipt,
    )
    def route_receipt(action_id: UUID4, response: Response):
        try:
            result = unavailable_store().broker_route_control_receipt(str(action_id))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Broker route receipt could not be verified") from error
        if result is None:
            raise HTTPException(404, "Broker route receipt not found")
        response.headers["Cache-Control"] = "no-store"
        return BrokerRouteControlReceipt.model_validate(result)

    @app.post(
        "/api/v1/brokers/{broker_id}/route-controls",
        dependencies=[Depends(authorization.require_control)],
        response_model=BrokerRouteControlReceipt,
    )
    def change_route(
        broker_id: Literal["PAPER"],
        command: BrokerRouteControlCommand,
        response: Response,
        session: OperatorSession = Depends(authorization.require_session),
    ):
        if command.operator_session_id != session.session_id:
            raise HTTPException(403, "Command session identity does not match")
        journal = unavailable_store()
        fingerprint = sha256(command.model_dump_json().encode()).hexdigest()
        action_id = str(command.action_id)
        try:
            prior = journal.broker_route_control_receipt(action_id)
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Broker route journal could not be verified") from error
        if prior is not None:
            if prior["request_sha256"] != fingerprint:
                raise HTTPException(
                    409, "Route action identity was reused with a different command"
                )
            response.headers["Cache-Control"] = "no-store"
            return BrokerRouteControlReceipt.model_validate(prior)

        reserved_at = clock()
        if not session.created_at <= reserved_at < session.expires_at:
            raise HTTPException(401, "Operator session expired before route admission")
        identity = str(uuid4())

        with control_lock:
            result: dict | None = None

            def decide(current_enabled: bool) -> bool:
                nonlocal result
                now = clock()
                if not session.created_at <= now < session.expires_at:
                    raise HTTPException(401, "Operator session expired before route admission")
                rejection = None
                if abs((now - command.client_timestamp).total_seconds()) > 60:
                    rejection = "STALE_COMMAND"
                elif command.expected_enabled is not current_enabled:
                    rejection = "STALE_STATE"
                elif command.requested_enabled and not current_enabled:
                    if risk.safety_state != SafetyState.NORMAL:
                        rejection = "SAFETY_STATE_BLOCKS_ENABLE"
                    elif router.snapshot()[0]["connection_state"] != "CONNECTED":
                        rejection = "BROKER_DISCONNECTED"
                    elif not router.eligibility(broker_id).allows_paper_orders():
                        rejection = "BROKER_NOT_ELIGIBLE"
                base_receipt = {
                    "schema_version": "1.0",
                    "mode": "PAPER",
                    "broker_id": broker_id,
                    "action_id": action_id,
                    "decision": "PENDING",
                    "expected_enabled": command.expected_enabled,
                    "requested_enabled": command.requested_enabled,
                    "operator_session_id": str(session.session_id),
                    "request_sha256": fingerprint,
                    "server_timestamp": now.isoformat(),
                    "trace_id": identity,
                    "policy_checks": [],
                }
                try:
                    result = journal.record_broker_route_control(
                        "broker-route:" + action_id,
                        command.reason,
                        base_receipt,
                        current_enabled=current_enabled,
                        rejection_reason=rejection,
                    )
                except OperatorReservationConflict as error:
                    raise HTTPException(409, "Route action identity is already assigned") from error
                except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                    raise HTTPException(
                        503, "Broker route decision could not be committed"
                    ) from error
                return result["authoritative_enabled"]

            try:
                router.transition_enabled(broker_id, decide)
            except HTTPException:
                raise
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Broker route state could not be updated") from error
        response.headers["Cache-Control"] = "no-store"
        if result is None:
            raise HTTPException(503, "Broker route outcome is unavailable")
        return BrokerRouteControlReceipt.model_validate(result)
