"""Versioned archival bundle for one fast decision and its advisory deep review."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.decision_gateway import AgentEnvelope
from ai_quant_os.deep_review import ReviewEnvelope
from ai_quant_os.world_state import WorldState


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"]
    run_id: str = Field(min_length=1, max_length=128)
    snapshot_ref: str = Field(min_length=1, max_length=128)
    decision: AgentEnvelope
    deep_review: ReviewEnvelope
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def linked_envelopes(self) -> "AgentRun":
        fast, review = self.decision, self.deep_review
        if (
            self.run_id != fast.trace_id
            or self.snapshot_ref != fast.world_state_version
            or review.world_state_version != self.snapshot_ref
            or review.task_id != fast.task_id
            or review.output.parent_trace_id != fast.trace_id
            or review.output.instrument_id != fast.output.instrument_id
            or review.trace_id == fast.trace_id
            or fast.input_refs != (self.snapshot_ref,)
            or review.input_refs != (self.snapshot_ref, fast.trace_id)
            or not fast.requires_escalation
            or fast.uncertainty != Decimal(1) - fast.confidence
            or review.uncertainty != Decimal(1) - review.confidence
            or not fast.created_at <= review.created_at < review.expires_at <= fast.expires_at
            or self.recorded_at < review.created_at
        ):
            raise ValueError("Agent run envelope provenance or expiry mismatch")
        completed = review.output.status == "COMPLETED"
        if completed:
            if (
                review.output.classification is None
                or not review.output.rationale
                or len(review.output.rationale) > 2000
                or not review.evidence_refs
            ):
                raise ValueError("Completed review is missing bounded evidence")
        elif (
            review.output.classification is not None
            or review.output.rationale is not None
            or review.confidence != 0
            or review.contradictions
        ):
            raise ValueError("Uncompleted review contains a candidate output")
        if (fast.output.next_route == "WAIT_FOR_DATA") != (review.output.status == "WAIT_FOR_DATA"):
            raise ValueError("Agent run review route mismatch")
        return self

    def audit_event(self) -> dict[str, str]:
        fast, review = self.decision, self.deep_review
        return {
            "action": "PAPER_CLASSIFICATION",
            "intent_id": "",
            "reason": fast.output.next_route,
            "run_id": self.run_id,
            "run_hash": hashlib.sha256(self.model_dump_json().encode()).hexdigest(),
            "trace_id": fast.trace_id,
            "task_id": fast.task_id,
            "world_state_version": fast.world_state_version,
            "snapshot_ref": self.snapshot_ref,
            "model_route": fast.model_route,
            "provider_version": fast.provider_version,
            "classification": fast.output.classification,
            "fallback_reasons": ",".join(fast.output.fallback_reasons),
            "expires_at": fast.expires_at.isoformat(),
            "deep_review_trace_id": review.trace_id,
            "deep_review_status": review.output.status,
            "deep_review_route": review.model_route,
            "deep_review_provider_version": review.provider_version,
            "deep_review_classification": review.output.classification or "",
            "deep_review_confidence": str(review.confidence),
            "deep_review_expires_at": review.expires_at.isoformat(),
        }


def verify_run_snapshot(run: AgentRun, snapshot: WorldState) -> None:
    if snapshot.world_state_version != run.snapshot_ref:
        raise ValueError("Agent run snapshot identity mismatch")
    item = next(
        (
            row
            for row in snapshot.instruments
            if row.instrument_id == run.decision.output.instrument_id
        ),
        None,
    )
    if item is None or any(
        envelope.created_at < snapshot.as_of
        or envelope.expires_at > snapshot.valid_until
        or not set(envelope.evidence_refs).issubset(item.evidence_refs)
        for envelope in (run.decision, run.deep_review)
    ):
        raise ValueError("Agent run snapshot evidence or expiry mismatch")
