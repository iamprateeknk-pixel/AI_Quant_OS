import asyncio
from datetime import timedelta

import pytest

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.strategy_builder import render_candidate
from ai_quant_os.strategy_builder_sources import builder_sources, candidate_for_context
from ai_quant_os.strategy_builder_agent import StrategyCandidate
from test_strategy_build_store import reviewed, candidate
from test_research_hypothesis_api import NOW


def context(review=None):
    return asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            builder_sources(review or reviewed(), now=NOW),
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=100000,
        ).compile("A09", "0.2.0", task_id="build", scope="SANDBOX")
    )


def test_renderer_revalidates_model_copy_before_interpolating_candidate_source():
    review = reviewed()
    forged = review.model_copy(
        update={
            "hypothesis": review.hypothesis.model_copy(
                update={"instrument_id": 'EURUSD"; injected'}
            )
        }
    )
    with pytest.raises(ValueError):
        render_candidate(
            review=forged,
            context_ref="agent-context:" + "d" * 64,
            task_id="build",
            sdk_source_pins=(("market_data.py", "e" * 64), ("strategy_contracts.py", "f" * 64)),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_ref": "strategy-candidate:" + "0" * 64},
        {"review_ref": "hypothesis-review:" + "0" * 64},
        {"contradictions": ()},
    ],
)
def test_builder_output_cannot_forge_source_bound_candidate(changes):
    compiled = context()
    bundle = candidate_for_context(compiled)
    output = StrategyCandidate(
        task_id=compiled.task_id,
        input_refs=compiled.input_refs,
        evidence_refs=compiled.input_refs,
        created_at=compiled.created_at,
        valid_until=compiled.valid_until,
        trace_id="fixture",
        contradictions=bundle.assumptions,
        candidate_ref=bundle.candidate_ref,
        review_ref=bundle.review.review_ref,
        genome=bundle.genome,
    )
    output.require_context(compiled)
    with pytest.raises(ValueError, match="retained context"):
        StrategyCandidate.model_validate({**output.model_dump(), **changes}).require_context(
            compiled
        )


def test_expired_review_refuses_source_capture_without_replacing_expiry():
    review = reviewed()
    with pytest.raises(PermissionError):
        builder_sources(review, now=review.valid_until)
    with pytest.raises(PermissionError):
        builder_sources(review, now=NOW - timedelta(seconds=1))


def test_template_retains_distinct_follow_and_fade_source_without_running_either():
    review = reviewed()
    following = candidate(review)
    fading = candidate(
        review.model_copy(
            update={
                "hypothesis": review.hypothesis.model_copy(
                    update={"rule": "FADE_PREVIOUS_CLOSE_DIRECTION"}
                )
            }
        )
    )
    assert following.files[0].sha256 != fading.files[0].sha256
    assert following.genome != fading.genome
    assert following.tests_status == fading.tests_status == "NOT_RUN"
    assert following.validation_status == fading.validation_status == "NOT_RUN"
