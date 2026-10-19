"""Checked review evidence and a fixed, explicitly unadmitted proposed-source SDK."""

from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext, EvidenceRecord, SourceBinding, verify_context
from ai_quant_os.hypothesis_review_contracts import HypothesisReview
from ai_quant_os.strategy_builder import render_candidate
from ai_quant_os.strategy_candidate_contracts import CandidateBundle


class BuilderObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime


class ReviewedHypothesisEvidence(BuilderObservation):
    review: HypothesisReview


class StrategySDKEvidence(BuilderObservation):
    profile: Literal["INERT_DIRECTION_TEMPLATE_V1"] = "INERT_DIRECTION_TEMPLATE_V1"
    interface: Literal["target_position(completed_bar_history)->long_short_flat_unit_target"] = (
        "target_position(completed_bar_history)->long_short_flat_unit_target"
    )
    execution_adapter_status: Literal["PROPOSED_SOURCE_RUNNER_UNAVAILABLE"] = (
        "PROPOSED_SOURCE_RUNNER_UNAVAILABLE"
    )
    source_pins: tuple[tuple[str, str], ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def checked_inventory(self):
        if tuple(name for name, _ in self.source_pins) != (
            "market_data.py",
            "strategy_contracts.py",
        ) or any(
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            for _, digest in self.source_pins
        ):
            raise ValueError("Invalid builder SDK source inventory")
        return self


class BuilderFeatureCatalog(BuilderObservation):
    features: tuple[Literal["PREVIOUS_CLOSED_BAR_DIRECTION"], ...] = (
        "PREVIOUS_CLOSED_BAR_DIRECTION",
    )
    max_history_bars: Literal[2] = 2
    allows_external_features: Literal[False] = False

    @model_validator(mode="after")
    def fixed_features(self):
        if self.features != ("PREVIOUS_CLOSED_BAR_DIRECTION",):
            raise ValueError("Unsupported candidate feature catalog")
        return self


def builder_sources(review: HypothesisReview, *, now: datetime) -> dict[str, SourceBinding]:
    review = HypothesisReview.model_validate_json(review.model_dump_json())
    if not review.reviewed_at <= now < review.valid_until:
        raise PermissionError("Candidate review is expired or future")
    pins = []
    for name in ("market_data.py", "strategy_contracts.py"):
        with Path(__file__).with_name(name).open("rb") as source:
            raw = source.read(65537)
        if not 0 < len(raw) <= 65536:
            raise ValueError("Builder SDK source exceeds bound")
        pins.append((name, sha256(raw).hexdigest()))
    sources = {}
    for name, payload, tool, resource in (
        (
            "hypothesis_spec",
            ReviewedHypothesisEvidence(as_of=now, review=review),
            "RESEARCH_MEMORY_READ",
            "GENERAL",
        ),
        (
            "strategy_sdk",
            StrategySDKEvidence(as_of=now, source_pins=tuple(pins)),
            "WORLD_STATE_READ",
            "GENERAL",
        ),
        ("feature_catalog", BuilderFeatureCatalog(as_of=now), "WORLD_STATE_READ", "GENERAL"),
    ):
        record = EvidenceRecord(
            source_id="strategy-builder:"
            + name
            + ":"
            + sha256(payload.model_dump_json().encode()).hexdigest(),
            as_of=now,
            valid_until=min(now + timedelta(seconds=5), review.valid_until),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, resource, fetch)
    return sources


def candidate_for_context(context: CompiledContext) -> CandidateBundle:
    verify_context(context)
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A09",
        "0.2.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Candidate builder requires its complete sandbox context")
    expected = (
        ("hypothesis_spec", "RESEARCH_MEMORY_READ", "GENERAL"),
        ("strategy_sdk", "WORLD_STATE_READ", "GENERAL"),
        ("feature_catalog", "WORLD_STATE_READ", "GENERAL"),
    )
    if (
        tuple((row.input_name, row.source_tool, row.resource_class) for row in context.inputs)
        != expected
    ):
        raise ValueError("Candidate context resource binding mismatch")
    review = ReviewedHypothesisEvidence.model_validate_json(context.inputs[0].payload_json)
    sdk = StrategySDKEvidence.model_validate_json(context.inputs[1].payload_json)
    features = BuilderFeatureCatalog.model_validate_json(context.inputs[2].payload_json)
    if not (
        review.as_of == sdk.as_of == features.as_of <= context.created_at
        and review.review.reviewed_at
        <= review.as_of
        < context.valid_until
        <= min(review.review.valid_until, review.as_of + timedelta(seconds=5))
    ):
        raise ValueError("Candidate review/source validity mismatch")
    return render_candidate(
        review=review.review,
        context_ref=context.context_ref,
        task_id=context.task_id,
        sdk_source_pins=sdk.source_pins,
    )
