"""Compact advisory views of checked candidate validation observations."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.candidate_validation_contracts import GATE_NAMES


class ValidationGateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str = Field(min_length=1, max_length=128)


class ValidationStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    holdout_net_pnl: Decimal = Field(allow_inf_nan=False)
    development_net_pnl: Decimal = Field(allow_inf_nan=False)
    nonnegative_window_fraction: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    pnl_currency: Literal["USD", "JPY"]
    requested_trials: int = Field(strict=True, ge=100, le=100000)
    requested_seed: int = Field(strict=True, ge=0, le=4294967295)
    closed_trade_count: int = Field(strict=True, ge=0, le=4096)


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_ref: str = Field(pattern=r"^candidate-(validation|prerequisite-failure):[a-f0-9]{64}$")
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    training_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    holdout_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    outcome: Literal["PREREQUISITE_FAILED", "REJECTED", "VALIDATION_INCOMPLETE"]
    gates: tuple[ValidationGateSummary, ...] = Field(min_length=15, max_length=15)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=15)
    statistics: ValidationStatistics | None
    evidence_basis: Literal["ARCHIVED_UNATTESTED_DESCRIPTIVE_VALIDATION"] = (
        "ARCHIVED_UNATTESTED_DESCRIPTIVE_VALIDATION"
    )

    @model_validator(mode="after")
    def coherent_observations(self):
        if tuple(row.name for row in self.gates) != GATE_NAMES:
            raise ValueError("Validation gate inventory changed")
        if any(row.state != "PENDING" for row in self.gates[7:]):
            raise ValueError("Independent acceptance remains pending")
        if self.outcome == "PREREQUISITE_FAILED":
            if (
                self.statistics is not None
                or not self.artifact_ref.startswith("candidate-prerequisite-failure:")
                or self.gates[0].state != "FAIL"
                or any(row.state != "PENDING" for row in self.gates[1:])
                or self.reasons != (self.gates[0].reason,)
            ):
                raise ValueError("Failed prerequisite cannot contain trading observations")
        elif self.statistics is None or not self.artifact_ref.startswith("candidate-validation:"):
            raise ValueError("Statistical scorecard requires checked numeric observations")
        else:
            failed = tuple(row for row in self.gates if row.state == "FAIL")
            pending = tuple(row for row in self.gates if row.state == "PENDING")
            if self.outcome != (
                "REJECTED" if failed else "VALIDATION_INCOMPLETE"
            ) or self.reasons != tuple(row.name + ":" + row.reason for row in (failed or pending)):
                raise ValueError("Validation summary outcome differs from its gate evidence")
        if len(set(self.reasons)) != len(self.reasons) or any(
            not row or len(row) > 256 for row in self.reasons
        ):
            raise ValueError("Bounded validation reasons required")
        return self

    @property
    def summary_sha256(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()
