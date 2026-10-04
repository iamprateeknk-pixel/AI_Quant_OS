"""Operator authorization to construct one inert candidate; no validation approval."""

from datetime import timedelta
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.research_hypothesis_contracts import ResearchHypothesis


class HypothesisReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    review_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    graph_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hypothesis: ResearchHypothesis
    rationale: str = Field(min_length=1, max_length=2048)
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime
    decision: Literal["AUTHORIZE_INERT_CANDIDATE_BUILD"] = "AUTHORIZE_INERT_CANDIDATE_BUILD"
    reviewer_basis: Literal["LOCAL_OPERATOR_TOKEN"] = "LOCAL_OPERATOR_TOKEN"
    alpha_acceptance: Literal["UNASSESSED"] = "UNASSESSED"
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False

    @model_validator(mode="after")
    def bounded_authorization(self):
        if not self.reviewed_at < self.valid_until <= self.reviewed_at + timedelta(hours=24):
            raise ValueError("Invalid candidate build authorization expiry")
        return self

    @property
    def review_ref(self) -> str:
        return "hypothesis-review:" + sha256(self.model_dump_json().encode()).hexdigest()


class ReviewWithdrawal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    review_ref: str = Field(pattern=r"^hypothesis-review:[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2048)
    withdrawn_at: AwareDatetime
    reviewer_basis: Literal["LOCAL_OPERATOR_TOKEN"] = "LOCAL_OPERATOR_TOKEN"
