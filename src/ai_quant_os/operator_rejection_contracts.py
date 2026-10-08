"""Terminal paper-control refusal; carries no safety transition authority."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.risk import SafetyState

RejectionReason = Literal["STALE_STATE", "STALE_COMMAND", "SAFETY_RECOVERY_FORBIDDEN"]


class OperatorControlRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    receipt_id: UUID
    action_id: UUID
    decision: Literal["REJECTED"]
    previous_state: SafetyState
    authoritative_state: SafetyState
    operator_session_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_timestamp: datetime
    trace_id: UUID
    rejection_reason: RejectionReason
    policy_checks: tuple[
        Literal["PAPER_ONLY"], Literal["VALID_SESSION"], Literal["FRESH_REAUTH"], RejectionReason
    ]

    @model_validator(mode="after")
    def valid_refusal(self):
        if (
            self.trace_id != self.receipt_id
            or self.server_timestamp.utcoffset() != timedelta(0)
            or self.previous_state != self.authoritative_state
            or self.policy_checks[-1] != self.rejection_reason
        ):
            raise ValueError("Invalid paper-control rejection binding")
        return self
