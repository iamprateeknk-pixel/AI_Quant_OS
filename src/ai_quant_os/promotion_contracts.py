"""Versioned promotion evidence contract with a gated promotion authority.

A :class:`PromotionDecision` authorizes promotion only when the full
promotion gate evidence is present on the record itself: the decision
is ``PROMOTED``, the Validation Fortress verdict is bound by hypothesis
id and by content-hash reference, the strategy council accepted, and
the decision timestamp does not predate the record. Any record claiming
approval without that complete evidence is invalid, and a ``PROMOTED``
decision without approval is invalid too. Records that do not claim
promotion (``WAIT_FOR_EVIDENCE`` / ``REJECTED`` with
``approved_by_policy`` false) need no gate evidence, so evidence
snapshots stored by earlier releases remain valid.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, model_validator


EvidenceReference = Annotated[StrictStr, Field(min_length=1, max_length=512)]


class PromotionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["NOT_RUN", "UNAVAILABLE", "PASS", "FAIL"]
    evidence_refs: tuple[EvidenceReference, ...] = Field(default=(), max_length=32)
    evaluated_at: AwareDatetime | None = None
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000)] | None = None

    @model_validator(mode="after")
    def require_evidence_for_completed_evaluation(self):
        completed = self.status in {"PASS", "FAIL"}
        if completed != bool(self.evidence_refs) or completed != (self.evaluated_at is not None):
            raise ValueError("Completed evaluations require timestamped evidence references")
        if not completed and self.reason is None:
            raise ValueError("Unrun or unavailable evaluations require an explicit reason")
        return self


class PromotionDecision(BaseModel):
    """Promotion evidence snapshot; approval requires complete gate evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: StrictStr = Field(min_length=1, max_length=256)
    current_champion_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    offline_eval: PromotionEvaluation
    replay_eval: PromotionEvaluation
    adversarial_eval: PromotionEvaluation
    shadow_eval: PromotionEvaluation
    canary_eval: PromotionEvaluation
    decision: Literal["WAIT_FOR_EVIDENCE", "PROMOTED", "REJECTED"] = "WAIT_FOR_EVIDENCE"
    rollback_target: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    approved_by_policy: bool = False
    fortress_hypothesis_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    fortress_verdict_ref: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    council_name: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    council_verdict: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    decided_at: AwareDatetime | None = None
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

    @model_validator(mode="after")
    def validate_promotion_gate(self):
        if self.decided_at is not None and self.decided_at < self.created_at:
            raise ValueError("Promotion decision cannot be decided before it is created")
        gate_complete = (
            self.decision == "PROMOTED"
            and bool(self.fortress_hypothesis_id)
            and self.fortress_verdict_ref is not None
            and bool(self.council_name)
            and self.council_verdict == "ACCEPTED"
            and self.decided_at is not None
            and self.decided_at >= self.created_at
        )
        if self.decision == "PROMOTED" and not self.approved_by_policy:
            raise ValueError("A PROMOTED decision must be approved by policy")
        if self.approved_by_policy and not gate_complete:
            raise ValueError(
                "Approval requires a PROMOTED decision with complete fortress "
                "and council gate evidence"
            )
        return self


def promotion_decision_schema() -> dict[str, object]:
    schema = PromotionDecision.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:ai-quant-os:promotion-decision:1.0"
    return schema
