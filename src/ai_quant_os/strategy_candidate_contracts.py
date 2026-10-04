"""Inert source/config/test artifacts and their exact strategy genome identity."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.hypothesis_review_contracts import HypothesisReview
from ai_quant_os.strategy_contracts import StrategyGenome


class ProposedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Literal["candidate.py", "test_candidate.py"]
    text: str = Field(min_length=1, max_length=8192)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def source_identity(self):
        if "\r" in self.text or sha256(self.text.encode()).hexdigest() != self.sha256:
            raise ValueError("Candidate file byte identity mismatch")
        return self


class CandidateBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    task_id: str = Field(min_length=1, max_length=128)
    review: HypothesisReview
    genome: StrategyGenome
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: tuple[ProposedFile, ...] = Field(min_length=2, max_length=2)
    sdk_source_pins: tuple[tuple[str, str], ...] = Field(min_length=2, max_length=2)
    feature: Literal["PREVIOUS_CLOSED_BAR_DIRECTION"] = "PREVIOUS_CLOSED_BAR_DIRECTION"
    entry_point: Literal["candidate:HypothesisDirection"] = "candidate:HypothesisDirection"
    execution_timing: Literal["NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"] = (
        "NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"
    )
    assumptions: tuple[str, ...]
    validation_status: Literal["NOT_RUN"] = "NOT_RUN"
    tests_status: Literal["NOT_RUN"] = "NOT_RUN"
    compiler_status: Literal["NOT_RUN"] = "NOT_RUN"
    isolation_verified: Literal[False] = False
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False
    runtime_registered: Literal[False] = False

    @model_validator(mode="after")
    def artifact_identity(self):
        if self.genome_sha256 != self.genome.fingerprint or tuple(
            row.path for row in self.files
        ) != ("candidate.py", "test_candidate.py"):
            raise ValueError("Candidate genome or source inventory mismatch")
        return self

    @property
    def candidate_ref(self) -> str:
        return "strategy-candidate:" + sha256(self.model_dump_json().encode()).hexdigest()
