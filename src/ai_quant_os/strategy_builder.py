"""Render declared source and tests as inert text; never import or run a candidate."""

from hashlib import sha256

from ai_quant_os.hypothesis_review_contracts import HypothesisReview
from ai_quant_os.strategy_candidate_contracts import CandidateBundle, ProposedFile
from ai_quant_os.strategy_contracts import StrategyGenome

ASSUMPTIONS = (
    "INPUT_HISTORY_CONTAINS_ONLY_COMPLETED_BARS",
    "EXECUTE_AT_NEXT_BAR_OPEN_WITHOUT_LOOKAHEAD",
    "SINGLE_INSTRUMENT_LONG_SHORT_FLAT_UNIT_TARGETS",
    "PROPOSED_SOURCE_NOT_SUPPORTED_BY_INSTALLED_BUILTIN_RUNNER",
    "BROKER_COSTS_AND_ALPHA_ACCEPTANCE_UNASSESSED",
    "INDEPENDENT_ISOLATED_TEST_AND_HOLDOUT_VALIDATION_REQUIRED",
)


def render_candidate(
    *,
    review: HypothesisReview,
    context_ref: str,
    task_id: str,
    sdk_source_pins: tuple[tuple[str, str], ...],
) -> CandidateBundle:
    review = HypothesisReview.model_validate_json(review.model_dump_json())
    sign = 1 if review.hypothesis.rule == "FOLLOW_PREVIOUS_CLOSE_DIRECTION" else -1
    instrument = review.hypothesis.instrument_id
    source = f'''"""Unverified research candidate. Import only inside an admitted isolated worker."""
from dataclasses import dataclass
from ai_quant_os.market_data import Bar


@dataclass(frozen=True)
class HypothesisDirection:
    def target_position(self, history: tuple[Bar, ...]) -> int:
        if any(bar.instrument_id != "{instrument}" for bar in history):
            raise ValueError("Unexpected candidate instrument")
        if len(history) < 2:
            return 0
        prior, latest = history[-2:]
        if prior.observed_at >= latest.observed_at:
            raise ValueError("Completed history must be chronological")
        direction = 1 if latest.close > prior.close else -1 if latest.close < prior.close else 0
        return {sign} * direction
'''
    test_source = f'''"""Proposed candidate tests; retained but NOT RUN on the parent host."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from ai_quant_os.market_data import Bar
from candidate import HypothesisDirection


def bar(minute, price, instrument="{instrument}"):
    p = Decimal(price)
    return Bar(instrument, datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute), p, p, p, p, Decimal(1))


@pytest.mark.parametrize("prices,target", [((),0), (("1",),0), (("1","2"),{sign}), (("2","1"),{-sign}), (("1","1"),0)])
def test_closed_direction_targets(prices,target):
    assert HypothesisDirection().target_position(tuple(bar(i,p) for i,p in enumerate(prices))) == target


def test_wrong_instrument_and_nonchronological_history_are_rejected():
    with pytest.raises(ValueError):
        HypothesisDirection().target_position((bar(0,"1","OTHER"),))
    with pytest.raises(ValueError):
        HypothesisDirection().target_position((bar(1,"1"),bar(0,"2")))
'''
    genome = StrategyGenome(
        "research-direction-" + review.hypothesis.signature[:16] + "@0.1.0", "BAR"
    )
    return CandidateBundle(
        context_ref=context_ref,
        task_id=task_id,
        review=review,
        genome=genome,
        genome_sha256=genome.fingerprint,
        files=tuple(
            ProposedFile(path=path, text=text, sha256=sha256(text.encode()).hexdigest())
            for path, text in (("candidate.py", source), ("test_candidate.py", test_source))
        ),
        sdk_source_pins=sdk_source_pins,
        assumptions=ASSUMPTIONS,
    )
