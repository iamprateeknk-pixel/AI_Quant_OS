"""Historical original-stage uncertainty; no candidate rejection or cleanup claim."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.candidate_validation_job_contracts import Digest, ValidationStageRecord


def interruption_boundary(stage):
    return {
        "TESTS": "PROPOSED_TEST_EXECUTION",
        "HOLDOUT": "HOLDOUT_EXECUTION",
        "PUBLICATION": "RESULT_PUBLICATION",
    }.get(stage, "DEVELOPMENT_EXECUTION")


class ValidationInterruption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    job_sha256: Digest
    stage: ValidationStageRecord
    audit_head_sha256: Digest
    audit_event_count: int = Field(strict=True, ge=1, le=8236)
    boundary: Literal[
        "PROPOSED_TEST_EXECUTION",
        "HOLDOUT_EXECUTION",
        "DEVELOPMENT_EXECUTION",
        "RESULT_PUBLICATION",
    ]
    outcome: Literal["VALIDATION_INTERRUPTED"] = "VALIDATION_INTERRUPTED"
    evidence_basis: Literal["ORIGINAL_AUDITED_STAGE_OUTCOME_UNCONFIRMED"] = (
        "ORIGINAL_AUDITED_STAGE_OUTCOME_UNCONFIRMED"
    )
    recovery_required: Literal[True] = True
    cleanup_verified: Literal[False] = False
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator(
        "recovery_required",
        "cleanup_verified",
        "isolation_verified",
        "promotion_allowed",
        mode="before",
    )
    @classmethod
    def exact_claims(cls, value, info):
        if value is not (info.field_name == "recovery_required"):
            raise ValueError(
                "Interruption observations cannot grant cleanup or acceptance authority"
            )
        return value

    @model_validator(mode="after")
    def interrupted_boundary(self):
        if self.stage.phase not in ("RESERVED", "PREPARED", "UNCERTAIN"):
            raise ValueError("Only unacknowledged stage outcomes are interruptions")
        expected = interruption_boundary(self.stage.stage)
        if self.boundary != expected:
            raise ValueError("Interruption boundary differs from original stage")
        return self

    @property
    def interruption_ref(self):
        return "validation-interruption:" + sha256(self.model_dump_json().encode()).hexdigest()
