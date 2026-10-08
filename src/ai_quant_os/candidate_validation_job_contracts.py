"""Pinned original-source validation stages with no acceptance authority."""

from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.candidate_validation_contracts import CandidateValidationPolicy
from ai_quant_os.strategy_prefix_release_contracts import PrefixReleaseDirectory
from ai_quant_os.strategy_prefix_run_contracts import StrategyRunImageInputs

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", strict=True)]


def validation_stage_name(index: int, stage_count: int) -> str:
    if type(stage_count) is not int or not 4 <= stage_count <= 2051:
        raise ValueError("Bounded original stage declaration required")
    if type(index) is not int or not 0 <= index < stage_count:
        raise ValueError("Validation stage outside original declaration")
    if index == 0:
        return "TESTS"
    if index == 1:
        return "HOLDOUT"
    if index == stage_count - 1:
        return "PUBLICATION"
    return "DEVELOPMENT:" + str(index - 2)


class ValidationJobManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    parents: tuple[PrefixReleaseDirectory, ...] = Field(min_length=1, max_length=128)
    candidate_test_manifest_sha256: Digest
    image_inputs: StrategyRunImageInputs
    development_folds: int = Field(strict=True, ge=1, le=2048)
    maximum_cooperative_pauses: Literal[16] = 16
    quantity: int = Field(default=1, strict=True, ge=1, le=500000)
    initial_balance: Decimal = Field(
        default=Decimal("100000"), gt=0, le=Decimal("1000000000000"), allow_inf_nan=False
    )
    policy: CandidateValidationPolicy = Field(default_factory=CandidateValidationPolicy)
    promotion_allowed: Literal[False] = False

    @field_validator("promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Validation jobs cannot grant authority")
        return value

    @property
    def stage_count(self) -> int:
        return self.development_folds + 3

    def stage_name(self, index: int) -> str:
        return validation_stage_name(index, self.stage_count)

    @property
    def job_sha256(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class ValidationStageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: int = Field(strict=True, ge=0, le=2050)
    stage: str = Field(pattern=r"^(TESTS|HOLDOUT|DEVELOPMENT:[0-9]{1,4}|PUBLICATION)$")
    phase: Literal["RESERVED", "PREPARED", "COMPLETED", "UNCERTAIN", "PAUSED"]
    attempt: int = Field(default=0, strict=True, ge=0, le=16)
    manifest_sha256: Digest | None = None
    result_sha256: Digest | None = None
    test_status: (
        Literal[
            "COMPILE_FAILED", "PROPOSED_TESTS_PASSED_UNATTESTED", "PROPOSED_TESTS_FAILED_UNATTESTED"
        ]
        | None
    ) = None
    artifact_ref: str | None = Field(
        default=None, pattern=r"^candidate-(validation|prerequisite-failure):[a-f0-9]{64}$"
    )
    reason: Literal["STAGE_OUTCOME_UNCONFIRMED", "COOPERATIVE_STOP_AFTER_OWNED_PREFIX"] | None = (
        None
    )
    promotion_allowed: Literal[False] = False

    @field_validator("promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Validation stages cannot grant authority")
        return value

    @model_validator(mode="after")
    def phase_evidence(self):
        complete = self.phase == "COMPLETED"
        expected_reason = {
            "UNCERTAIN": "STAGE_OUTCOME_UNCONFIRMED",
            "PAUSED": "COOPERATIVE_STOP_AFTER_OWNED_PREFIX",
        }.get(self.phase)
        if self.reason != expected_reason:
            raise ValueError("Stopped stages require their exact typed reason")
        if (self.result_sha256 is not None) != complete:
            raise ValueError("Only complete stages contain a result pin")
        if complete or self.phase in ("PREPARED", "PAUSED"):
            if self.manifest_sha256 is None:
                raise ValueError("Prepared stages require original manifest pins")
        if (self.test_status is not None) != (complete and self.stage == "TESTS"):
            raise ValueError("Original proposed-test status required only at test completion")
        if (self.artifact_ref is not None) != (complete and self.stage == "PUBLICATION"):
            raise ValueError("Only complete publication contains an artifact reference")
        return self
