"""Original command custody before terminal publication; no execution claim."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.risk import SafetyState


class OperatorControlPending(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    receipt_id: UUID
    action_id: UUID
    decision: Literal["PENDING"]
    previous_state: SafetyState
    authoritative_state: SafetyState
    operator_session_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_timestamp: datetime
    trace_id: UUID
    policy_checks: tuple[
        Literal["PAPER_ONLY"],
        Literal["VALID_SESSION"],
        Literal["FRESH_REAUTH"],
        Literal["RESERVED_NOT_APPLIED"],
    ]

    @model_validator(mode="after")
    def valid_reservation(self):
        if (
            self.receipt_id != self.trace_id
            or self.server_timestamp.utcoffset() != timedelta(0)
            or self.previous_state != self.authoritative_state
        ):
            raise ValueError("Invalid original control reservation")
        return self
