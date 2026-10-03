"""Immutable historical CSV identity and classification receipt contract."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ResearchDataReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    dataset_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    resource_class: Literal["TRAINING", "HOLDOUT"]
    provenance: Literal["OPERATOR_SUPPLIED_CSV"] = "OPERATOR_SUPPLIED_CSV"
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    row_count: int = Field(strict=True, ge=2, le=4096)
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    received_at: AwareDatetime
    market_authenticity: Literal["UNVERIFIED"] = "UNVERIFIED"
    statistical_independence: Literal["UNASSESSED"] = "UNASSESSED"
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False

    @model_validator(mode="after")
    def chronological_identity(self) -> "ResearchDataReceipt":
        if not self.first_observed_at < self.last_observed_at <= self.received_at:
            raise ValueError("Invalid research evidence chronology")
        if self.dataset_ref != research_dataset_ref(
            self.source_id, self.raw_sha256, self.resource_class
        ):
            raise ValueError("Invalid research evidence identity")
        if any(
            value.utcoffset().total_seconds() != 0
            for value in (self.first_observed_at, self.last_observed_at, self.received_at)
        ):
            raise ValueError("Research evidence timestamps must be UTC")
        return self


def research_dataset_ref(source_id: str, raw_sha256: str, resource_class: str) -> str:
    material = json.dumps(
        ["research-data-v1", source_id, raw_sha256, resource_class], separators=(",", ":")
    )
    return "research-data:" + sha256(material.encode()).hexdigest()
