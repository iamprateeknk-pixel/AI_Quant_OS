"""Public session identity and private credential fingerprint."""

from datetime import datetime, timedelta
from uuid import UUID
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, UUID4, model_validator


SESSION_TTL = timedelta(minutes=15)


class OperatorSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_epoch: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def valid_lifetime(self):
        if any(t.utcoffset() != timedelta(0) for t in (self.created_at, self.expires_at)):
            raise ValueError("UTC session timestamps required")
        if self.expires_at != self.created_at + SESSION_TTL:
            raise ValueError("Invalid session lifetime")
        return self

    def public(self) -> dict[str, str]:
        return {
            "mode": "PAPER",
            "session_id": str(self.session_id),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class SessionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["PAPER"] = "PAPER"
    session_id: UUID4
    created_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["CURRENT", "ACTIVE", "EXPIRED", "FUTURE"]

    @model_validator(mode="after")
    def lifetime(self):
        if (
            any(t.utcoffset() != timedelta(0) for t in (self.created_at, self.expires_at))
            or self.expires_at != self.created_at + SESSION_TTL
        ):
            raise ValueError("Invalid public session lifetime")
        return self


class SessionInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    scope: Literal["LOCAL_SINGLE_OWNER"] = "LOCAL_SINGLE_OWNER"
    observed_at: AwareDatetime
    current_session_id: UUID4
    source_head: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_event_count: int = Field(strict=True, ge=1, le=8192)
    sessions: tuple[SessionMetadata, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def observed_sessions(self):
        identities = [row.session_id for row in self.sessions]
        if (
            self.observed_at.utcoffset() != timedelta(0)
            or len(set(identities)) != len(identities)
            or self.current_session_id not in identities
            or self.source_head == "0" * 64
            or self.source_event_count < len(identities)
        ):
            raise ValueError("Invalid session inventory observation")
        for row in self.sessions:
            expected = (
                "CURRENT"
                if row.session_id == self.current_session_id
                else "FUTURE"
                if self.observed_at < row.created_at
                else "EXPIRED"
                if self.observed_at >= row.expires_at
                else "ACTIVE"
            )
            if row.status != expected or (
                expected == "CURRENT" and not row.created_at <= self.observed_at < row.expires_at
            ):
                raise ValueError("Session state does not match observation")
        return self


class SessionRevocationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    status: Literal["REVOKED"] = "REVOKED"
    target_session_id: UUID4
    actor_session_id: UUID4
    current_session_revoked: bool = Field(strict=True)
    revoked_at: AwareDatetime
    source_head: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def revocation_scope(self):
        if (
            self.revoked_at.utcoffset() != timedelta(0)
            or self.current_session_revoked != (self.target_session_id == self.actor_session_id)
            or self.source_head == "0" * 64
        ):
            raise ValueError("Invalid session revocation receipt")
        return self
