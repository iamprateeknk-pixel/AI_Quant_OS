"""Public session identity and private credential fingerprint."""

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
