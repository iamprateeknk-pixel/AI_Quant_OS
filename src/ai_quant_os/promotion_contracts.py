"""Fail-closed versioned promotion evidence contract; no promotion authority."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, model_validator


class PromotionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["NOT_RUN", "UNAVAILABLE", "PASS", "FAIL"]
    evidence_refs: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    evaluated_at: AwareDatetime | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_evidence_for_completed_evaluation(self):
        completed = self.status in {"PASS", "FAIL"}
        if completed != bool(self.evidence_refs) or completed != (self.evaluated_at is not None):
            raise ValueError("Completed evaluations require timestamped evidence references")
        if not completed and self.reason is None:
            raise ValueError("Unrun or unavailable evaluations require an explicit reason")
        return self


class PromotionDecision(BaseModel):
    """Promotion evidence snapshot that cannot authorize promotion in this release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: StrictStr = Field(min_length=1, max_length=256)
    current_champion_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    offline_eval: PromotionEvaluation
    replay_eval: PromotionEvaluation
    adversarial_eval: PromotionEvaluation
    shadow_eval: PromotionEvaluation
    canary_eval: PromotionEvaluation
    decision: Literal["WAIT_FOR_EVIDENCE"] = "WAIT_FOR_EVIDENCE"
    rollback_target: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    approved_by_policy: Literal[False] = False
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_rollback_and_evidence_time(self):
        if self.rollback_target != self.current_champion_id:
            raise ValueError("Rollback target must match the current champion")
        evaluations = (
            self.offline_eval,
            self.replay_eval,
            self.adversarial_eval,
            self.shadow_eval,
            self.canary_eval,
        )
        if any(
            evaluation.evaluated_at is not None and evaluation.evaluated_at > self.created_at
            for evaluation in evaluations
        ):
            raise ValueError("Evaluation evidence cannot postdate the decision")
        return self


def promotion_decision_schema() -> dict[str, object]:
    schema = PromotionDecision.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:promotion-decision:1.0"
    return schema
