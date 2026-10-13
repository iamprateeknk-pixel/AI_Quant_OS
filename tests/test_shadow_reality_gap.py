from datetime import timedelta
from decimal import Decimal

from test_shadow_portfolio import shadow_fixture


def test_reality_gap_policy_is_bound_at_assignment_and_survives_restart(tmp_path):
    import pytest

    from ai_quant_os.shadow_pipeline import assign_shadow
    from ai_quant_os.shadow_reality_gap import RealityGapPolicy
    from ai_quant_os.shadow_store import ShadowStore

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    policy = RealityGapPolicy(60, 2, 2, Decimal("100"))
    assign_shadow(assignment, store, candidates, strategies, reality_gap_policy=policy)

    restarted = ShadowStore(store.path)
    assert restarted.reality_gap_policy(assignment.shadow_id) == policy
    with pytest.raises(ValueError, match="cannot change"):
        assign_shadow(
            assignment,
            restarted,
            candidates,
            strategies,
            reality_gap_policy=RealityGapPolicy(60, 2, 2, Decimal("150")),
        )

    legacy_assignment, legacy_store, legacy_candidates, legacy_strategies = shadow_fixture(
        tmp_path / "legacy"
    )
    assign_shadow(legacy_assignment, legacy_store, legacy_candidates, legacy_strategies)
    with pytest.raises(ValueError, match="cannot change"):
        assign_shadow(
            legacy_assignment,
            legacy_store,
            legacy_candidates,
            legacy_strategies,
            reality_gap_policy=policy,
        )
    assert legacy_store.reality_gap_policy(legacy_assignment.shadow_id) is None


def test_shadow_reality_gap_needs_minimum_forward_evidence(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_reality_gap import RealityGapPolicy, assess_shadow_reality_gap
    from ai_quant_os.market_data import Bar

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    policy = RealityGapPolicy(100, 2, 2, Decimal("100"))
    assign_shadow(assignment, store, candidates, strategies, reality_gap_policy=policy)
    bars = tuple(
        Bar(
            "EURUSD",
            assignment.assigned_at + timedelta(minutes=index + 1),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for index, price in enumerate(("1.1", "1.2", "1.3", "1.2", "1.1", "1.2"))
    )
    report = observe_shadow(
        assignment.shadow_id, "small-sample", bars, store, candidates, strategies
    )
    assessment = assess_shadow_reality_gap(report, policy)

    assert assessment.state == "UNASSESSED"
    assert "INSUFFICIENT_SHADOW_BARS" in assessment.reasons
    assert candidates.get(assignment.candidate.manifest.candidate_id).state == "CHALLENGER"


def test_adverse_forward_shadow_gap_demotes_and_blocks_candidate(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_reality_gap import RealityGapPolicy
    from ai_quant_os.market_data import Bar

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    policy = RealityGapPolicy(6, 2, 2, Decimal("100"))
    assign_shadow(assignment, store, candidates, strategies, reality_gap_policy=policy)
    bars = tuple(
        Bar(
            "EURUSD",
            assignment.assigned_at + timedelta(minutes=index + 1),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for index, price in enumerate(("1.1", "1.2", "1.3", "1.2", "1.1", "1.2"))
    )
    report = observe_shadow(
        assignment.shadow_id,
        "adverse-forward-window",
        bars,
        store,
        candidates,
        strategies,
    )

    assert report.outcome == "OBSERVED"
    view = candidates.get(assignment.candidate.manifest.candidate_id)
    assert view.state == "DEMOTED" and view.revision == 2
    import json
    import sqlite3

    with sqlite3.connect(candidates.path) as db:
        stored_events = [
            json.loads(row[0]) for row in db.execute("SELECT blob FROM candidate_events")
        ]
    event = next(row for row in stored_events if row["action"] == "DEMOTED")
    assert event["evidence"]["report_sha256"] == report.fingerprint
    assert event["evidence"]["policy_ref"] == policy.content_ref
    assert Decimal(event["evidence"]["gap_bps"]) <= -Decimal(event["evidence"]["threshold_bps"])
    from ai_quant_os.research_candidate_store import CandidateStore

    assert CandidateStore(candidates.path).get(assignment.candidate.manifest.candidate_id) == view
    assert (
        assign_shadow(assignment, store, candidates, strategies, reality_gap_policy=policy)
        == assignment
    )
    assert (
        observe_shadow(
            assignment.shadow_id,
            "adverse-forward-window",
            bars,
            store,
            candidates,
            strategies,
        )
        == report
    )
    later = Bar(
        "EURUSD",
        bars[-1].observed_at + timedelta(minutes=1),
        Decimal("1.2"),
        Decimal("1.2"),
        Decimal("1.2"),
        Decimal("1.2"),
        Decimal(100),
    )
    import pytest

    with pytest.raises(ValueError, match="lifecycle changed"):
        observe_shadow(
            assignment.shadow_id, "after-demotion", (later,), store, candidates, strategies
        )
    assert candidates.get(assignment.candidate.manifest.candidate_id).state == "DEMOTED"
