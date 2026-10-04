from datetime import timedelta
import importlib.util
import sqlite3

import pytest

from ai_quant_os.hypothesis_review_contracts import HypothesisReview
from ai_quant_os.research_hypothesis_contracts import ResearchHypothesis
from ai_quant_os.strategy_builder import render_candidate
from test_research_hypothesis_api import NOW


def reviewed():
    from ai_quant_os.research_data_contracts import research_dataset_ref

    return HypothesisReview(
        review_id="review",
        run_id="research",
        graph_record_sha256="a" * 64,
        hypothesis=ResearchHypothesis(
            dataset_ref=research_dataset_ref("synthetic", "b" * 64, "TRAINING"),
            dataset_fingerprint="c" * 64,
            instrument_id="EURUSD",
            development_end=NOW - timedelta(days=1),
            rule="FOLLOW_PREVIOUS_CLOSE_DIRECTION",
            description="Test continuation after costs.",
            eligible_pairs=10,
            same_direction_pairs=10,
            observed_direction_persistence="1",
            expected_costs={
                "status": "UNAVAILABLE",
                "spread_observation_count": 0,
                "minimum_recorded_spread_bps": None,
                "maximum_recorded_spread_bps": None,
            },
        ),
        rationale="Independent validation required.",
        reviewed_at=NOW,
        valid_until=NOW + timedelta(hours=1),
    )


def store(path, **options):
    assert importlib.util.find_spec("ai_quant_os.strategy_build_store") is not None
    from ai_quant_os.strategy_build_store import StrategyBuildStore

    return StrategyBuildStore(path, **options)


def candidate(review):
    return render_candidate(
        review=review,
        context_ref="agent-context:" + "d" * 64,
        task_id="build",
        sdk_source_pins=(("market_data.py", "e" * 64), ("strategy_contracts.py", "f" * 64)),
    )


def test_review_withdrawal_and_candidate_retain_exact_identity_across_restart(tmp_path):
    path = tmp_path / "build.sqlite3"
    review = reviewed()
    artifact = candidate(review)
    with store(path) as db:
        assert db.put_review(review) == review
        assert db.put_candidate(artifact, now=NOW) == artifact
        assert db.put_candidate(artifact, now=NOW) == artifact
        withdrawal = db.withdraw(review.review_ref, reason="Need more evidence.", now=NOW)
        with pytest.raises(PermissionError):
            db.active(review.review_ref, now=NOW)
    with store(path) as db:
        assert db.get_review(review.review_ref) == review
        assert db.get_candidate(artifact.candidate_ref) == artifact
        assert db.withdrawal(review.review_ref) == withdrawal


def test_expired_and_withdrawn_review_cannot_publish_even_existing_candidate(tmp_path):
    review = reviewed()
    with store(tmp_path / "build.sqlite3") as db:
        db.put_review(review)
        artifact = candidate(review)
        with pytest.raises(PermissionError):
            db.put_candidate(artifact, now=review.valid_until)
        assert db.get_candidate(artifact.candidate_ref) is None
        db.put_candidate(artifact, now=NOW)
        db.withdraw(review.review_ref, reason="Stop.", now=NOW)
        with pytest.raises(PermissionError):
            db.put_candidate(artifact, now=NOW)


def test_candidate_and_journal_corruption_is_rejected_without_healing(tmp_path):
    path = tmp_path / "build.sqlite3"
    review = reviewed()
    with store(path) as db:
        db.put_review(review)
        db.put_candidate(candidate(review), now=NOW)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM strategy_candidates")
    with pytest.raises(ValueError, match="binding"):
        store(path)


def test_existing_zero_file_and_review_capacity_are_refused(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="metadata"):
        store(empty)
    review = reviewed()
    with store(tmp_path / "build.sqlite3", capacity=1) as db:
        db.put_review(review)
        with pytest.raises(ValueError, match="capacity"):
            db.put_review(review.model_copy(update={"review_id": "second"}))
