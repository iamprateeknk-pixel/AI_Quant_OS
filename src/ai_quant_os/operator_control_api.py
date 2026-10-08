"""Session-bound paper controls with transactional state comparison and receipts."""

from datetime import datetime, timedelta
from hashlib import sha256
import sqlite3
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ai_quant_os.operator_session_contracts import OperatorSession
from ai_quant_os.risk import SafetyState
from ai_quant_os.operator_rejection_contracts import OperatorControlRejection


class OperatorControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    requested_action: Literal["NO_NEW_RISK", "FLATTEN_ONLY", "HALT"]
    reason: str = Field(min_length=1, max_length=512)
    operator_session_id: UUID
    client_timestamp: datetime
    expected_current_state: SafetyState
    confirmation: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def confirmed(self):
        if not self.reason.strip() or self.client_timestamp.utcoffset() != timedelta(0):
            raise ValueError("Reason and UTC timestamp required")
        if (
            self.requested_action in ("HALT", "FLATTEN_ONLY")
            and self.confirmation != self.requested_action
        ):
            raise ValueError("Exact typed confirmation required")
        return self


class OperatorControlReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    receipt_id: UUID
    action_id: UUID
    decision: Literal["ACCEPTED"]
    previous_state: SafetyState
    authoritative_state: Literal["NO_NEW_RISK", "FLATTEN_ONLY", "HALT"]
    operator_session_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_timestamp: datetime
    trace_id: UUID
    policy_checks: tuple[
        Literal["PAPER_ONLY"],
        Literal["VALID_SESSION"],
        Literal["FRESH_REAUTH"],
        Literal["EXPECTED_STATE"],
        Literal["MONOTONIC_SAFETY"],
    ]

    @model_validator(mode="after")
    def valid_trace(self):
        if self.trace_id != self.receipt_id or self.server_timestamp.utcoffset() != timedelta(0):
            raise ValueError("Invalid operator receipt trace/time")
        return self


ControlOutcome = Annotated[
    OperatorControlReceipt | OperatorControlRejection, Field(discriminator="decision")
]


def install_operator_control_routes(app: FastAPI, authorization, store, risk, lock, clock):
    def retained(action_id):
        try:
            prior = store.operator_control_receipt("operator:" + str(action_id))
            if prior is not None:
                TypeAdapter(ControlOutcome).validate_python(prior)
        except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
            raise HTTPException(503, "Operator receipt could not be verified") from error
        return prior

    @app.get(
        "/api/v1/operator/controls/{action_id}",
        dependencies=[Depends(authorization.require_session)],
        response_model=ControlOutcome,
    )
    def receipt(action_id: UUID, response: Response):
        result = retained(action_id)
        if result is None:
            raise HTTPException(404, "Operator receipt not found")
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.post(
        "/api/v1/operator/controls",
        dependencies=[Depends(authorization.require_control)],
        response_model=ControlOutcome,
    )
    def transition(
        command: OperatorControl,
        response: Response,
        session: OperatorSession = Depends(authorization.require_session),
    ):
        if command.operator_session_id != session.session_id:
            raise HTTPException(403, "Command session identity does not match")
        fingerprint = sha256(command.model_dump_json().encode()).hexdigest()
        with lock:
            now = clock()
            if not session.created_at <= now < session.expires_at:
                raise HTTPException(401, "Operator session expired before command admission")
            previous = retained(command.action_id)
            if previous is not None:
                if previous.get("request_sha256") != fingerprint:
                    raise HTTPException(409, "Action identity reused with a different command")
                response.headers["Cache-Control"] = "no-store"
                return previous
            identity = str(uuid4())
            result = {
                "schema_version": "1.0",
                "mode": "PAPER",
                "receipt_id": identity,
                "action_id": str(command.action_id),
                "decision": "ACCEPTED",
                "previous_state": command.expected_current_state.value,
                "authoritative_state": command.requested_action,
                "operator_session_id": str(session.session_id),
                "request_sha256": fingerprint,
                "server_timestamp": now.isoformat(),
                "trace_id": identity,
                "policy_checks": [
                    "PAPER_ONLY",
                    "VALID_SESSION",
                    "FRESH_REAUTH",
                    "EXPECTED_STATE",
                    "MONOTONIC_SAFETY",
                ],
            }
            state = SafetyState(command.requested_action)
            current = store.safety_state()
            rejection = None
            if abs((now - command.client_timestamp).total_seconds()) > 60:
                rejection = "STALE_COMMAND"
            elif command.expected_current_state != current:
                rejection = "STALE_STATE"
            elif list(SafetyState).index(state) < list(SafetyState).index(current):
                rejection = "SAFETY_RECOVERY_FORBIDDEN"
            if rejection is not None:
                result.update(
                    decision="REJECTED",
                    previous_state=current.value,
                    authoritative_state=current.value,
                    rejection_reason=rejection,
                    policy_checks=["PAPER_ONLY", "VALID_SESSION", "FRESH_REAUTH", rejection],
                )
                result = OperatorControlRejection.model_validate(result).model_dump(mode="json")
                try:
                    store.record_operator_rejection(
                        "operator:" + str(command.action_id), command.reason, result
                    )
                except ValueError as error:
                    raise HTTPException(
                        409, "Safety state changed before rejection publication"
                    ) from error
                except sqlite3.IntegrityError as error:
                    previous = retained(command.action_id)
                    if previous and previous.get("request_sha256") == fingerprint:
                        return previous
                    raise HTTPException(409, "Action identity already reserved") from error
                response.headers["Cache-Control"] = "no-store"
                return result
            try:
                risk.set_safety_state(
                    state,
                    before_change=lambda: store.record_control(
                        "operator:" + str(command.action_id),
                        state,
                        command.reason,
                        result,
                        expected_state=command.expected_current_state,
                    ),
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            except sqlite3.IntegrityError as error:
                # Another API process may have committed the exact same action.
                previous = retained(command.action_id)
                if previous and previous.get("request_sha256") == fingerprint:
                    return previous
                raise HTTPException(409, "Action identity already reserved") from error
            response.headers["Cache-Control"] = "no-store"
            return result
