from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")


def test_daily_comparison_binds_paper_and_shadow_to_same_candidate_and_interval(tmp_path):
    from test_daily_evidence import END, START, checkpoints
    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.market_data import Bar
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    prior = observe_shadow(
        assignment.shadow_id,
        "prior-period",
        forward_bars(assignment)[:3],
        shadow_store,
        candidates,
        strategies,
    )
    day_bars = tuple(
        Bar(
            "EURUSD",
            START + timedelta(minutes=5 * index),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for index, price in enumerate(("1.1", "1.2", "1.3", "1.2"), start=1)
    )
    first_batch = observe_shadow(
        assignment.shadow_id,
        "daily-period-first-batch",
        day_bars[:2],
        shadow_store,
        candidates,
        strategies,
    )
    current = observe_shadow(
        assignment.shadow_id,
        "daily-period-second-batch",
        day_bars[2:],
        shadow_store,
        candidates,
        strategies,
    )

    opening, closing = checkpoints()
    candidate_id = assignment.candidate.manifest.candidate_id
    genome = assignment.candidate.report.holdout.binding.genome
    strategy_id, strategy_version = genome.strategy_ref.split("@")
    owners = tuple(
        replace(
            owner,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            candidate_assignment_id="paper-assignment",
            candidate_id=candidate_id,
            genome_sha256=genome.fingerprint,
        )
        if owner.order_id == "sell"
        else owner
        for owner in closing.owners
    )
    closing = replace(closing, owners=owners)
    daily = DailyEvidence(opening, closing, build_daily_report(START.date(), opening, closing), END)
    daily_execution = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (), published_at=END
    )

    from ai_quant_os.paper_shadow_comparison import compare_daily_paper_to_shadow

    comparison = compare_daily_paper_to_shadow(daily_execution, prior, first_batch)

    assert comparison.day == START.date()
    assert comparison.candidate_id == candidate_id
    assert comparison.genome_sha256 == genome.fingerprint
    assert comparison.paper_fill_count == 1
    assert comparison.paper_cash_flow_quote == Decimal("4.6")
    assert comparison.paper_fee_cash_flow_quote == Decimal("-0.2")
    assert comparison.shadow_fill_count == len(first_batch.run.result.fills) - len(
        prior.run.result.fills
    )
    assert comparison.shadow_cash_flow_quote == sum(
        (
            -(fill.quantity if fill.side == "BUY" else -fill.quantity) * fill.price - fill.fee
            for fill in first_batch.run.result.fills[len(prior.run.result.fills) :]
        ),
        Decimal(0),
    )
    assert comparison.promotion_allowed is False
    assert comparison.causal_attribution_allowed is False

    from ai_quant_os.evidence_store import EvidenceStore

    archive = EvidenceStore(tmp_path / "paper.evidence.sqlite3")
    archive.checkpoint(opening)
    archive.checkpoint(closing)
    archive.publish(daily)
    archive.publish_daily_execution(daily_execution)
    from ai_quant_os.api import create_app
    from fastapi.testclient import TestClient

    app = create_app(
        operator_token="operator",
        clock=lambda: END,
        paper_db_path=tmp_path / "paper.sqlite3",
        research_workspace=tmp_path,
    )
    with TestClient(app) as client:
        unauthorized = client.post(
            f"/api/v1/paper/evidence/daily/{START.date()}/shadow-comparison",
            json={"shadow_id": assignment.shadow_id, "sequence": 3},
        )
        response = client.post(
            f"/api/v1/paper/evidence/daily/{START.date()}/shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            json={"shadow_id": assignment.shadow_id, "sequence": 3},
        )
        missing_shadow = client.post(
            f"/api/v1/paper/evidence/daily/{START.date()}/shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            json={"shadow_id": "unknown-shadow", "sequence": 3},
        )
        extra_input = client.post(
            f"/api/v1/paper/evidence/daily/{START.date()}/shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            json={"shadow_id": assignment.shadow_id, "sequence": 3, "cash": "999"},
        )
        range_response = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            params={
                "shadow_id": assignment.shadow_id,
                "start": START.date().isoformat(),
                "end": (START.date() + timedelta(days=1)).isoformat(),
            },
        )
        unauthorized_range = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            params={
                "shadow_id": assignment.shadow_id,
                "start": START.date().isoformat(),
                "end": START.date().isoformat(),
            },
        )
        invalid_range = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            params={
                "shadow_id": assignment.shadow_id,
                "start": START.date().isoformat(),
                "end": (START.date() + timedelta(days=366)).isoformat(),
            },
        )
        empty_range = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            params={
                "shadow_id": assignment.shadow_id,
                "start": (START.date() + timedelta(days=10)).isoformat(),
                "end": (START.date() + timedelta(days=10)).isoformat(),
            },
        )
    assert unauthorized.status_code == 401
    assert response.status_code == 200, response.text
    assert range_response.status_code == 200, range_response.text
    assert unauthorized_range.status_code == 401
    assert invalid_range.status_code == 422
    assert empty_range.status_code == 200
    assert empty_range.json()["coverage"] == "NO_EVIDENCE"
    assert missing_shadow.status_code == 404
    assert extra_input.status_code == 422
    assert response.json()["paper_evidence_sha256"] == comparison.paper_evidence_sha256
    expected_shadow_fills = current.run.result.fills[len(prior.run.result.fills) :]
    expected_shadow_cash = sum(
        (
            -(fill.quantity if fill.side == "BUY" else -fill.quantity) * fill.price - fill.fee
            for fill in expected_shadow_fills
        ),
        Decimal(0),
    )
    assert response.json()["shadow_fill_count"] == len(expected_shadow_fills)
    assert response.json()["shadow_cash_flow_quote"] == str(expected_shadow_cash)
    assert response.headers["cache-control"] == "no-store"
    range_report = range_response.json()
    assert range_report["coverage"] == "PARTIAL"
    assert range_report["covered_days"] == [START.date().isoformat()]
    assert range_report["missing_days"] == [(START.date() + timedelta(days=1)).isoformat()]
    assert range_report["days"][0]["comparison"]["shadow_cash_flow_quote"] == str(
        expected_shadow_cash
    )
    assert range_report["days"][1]["missing_reason"] == "DAILY_EXECUTION_MISSING"
    assert range_report["totals"]["shadow_cash_flow_quote"] == str(expected_shadow_cash)
    assert range_report["promotion_allowed"] is False
    assert range_report["causal_attribution_allowed"] is False
    assert range_response.headers["cache-control"] == "no-store"

    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from pydantic import TypeAdapter
    from ai_quant_os.daily_api import DailyShadowComparisonInput
    from ai_quant_os.paper_shadow_comparison import (
        DailyPaperShadowAggregate,
        PaperShadowComparison,
    )

    schema_dir = Path(__file__).resolve().parents[1] / "docs" / "schemas"
    for name, model, schema_id in (
        (
            "daily-shadow-comparison-input-v1.schema.json",
            DailyShadowComparisonInput,
            "urn:ai-quant-os:contract:daily-shadow-comparison-input:1.0",
        ),
        (
            "paper-shadow-comparison-v1.schema.json",
            TypeAdapter(PaperShadowComparison),
            "urn:ai-quant-os:contract:paper-shadow-comparison:1.0",
        ),
        (
            "daily-paper-shadow-aggregate-v1.schema.json",
            TypeAdapter(DailyPaperShadowAggregate),
            "urn:ai-quant-os:contract:daily-paper-shadow-aggregate:1.0",
        ),
    ):
        schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
        expected = (
            model.model_json_schema(mode="validation")
            if isinstance(model, type)
            else model.json_schema(mode="validation")
        )
        expected.update(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": schema_id,
            }
        )
        assert schema == expected
        Draft202012Validator.check_schema(schema)

    boundary_bars = tuple(
        Bar(
            "EURUSD",
            observed_at,
            Decimal("1.2"),
            Decimal("1.2"),
            Decimal("1.2"),
            Decimal("1.2"),
            Decimal(100),
        )
        for observed_at in (
            START + timedelta(hours=23, minutes=55),
            START + timedelta(days=1),
        )
    )
    observe_shadow(
        assignment.shadow_id,
        "cross-day-period",
        boundary_bars,
        shadow_store,
        candidates,
        strategies,
    )
    with TestClient(app) as client:
        cross_day_response = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            params={
                "shadow_id": assignment.shadow_id,
                "start": START.date().isoformat(),
                "end": START.date().isoformat(),
            },
        )
    assert cross_day_response.status_code == 200, cross_day_response.text
    assert cross_day_response.json()["days"][0]["missing_reason"] == "CROSS_DAY_BATCH"

    import sqlite3

    with sqlite3.connect(tmp_path / "shadow.sqlite3") as database:
        database.execute(
            "UPDATE shadow_batches SET report=? WHERE command_id=?",
            (b"{}", "daily-period-first-batch"),
        )
    with TestClient(app) as client:
        corrupt_response = client.post(
            f"/api/v1/paper/evidence/daily/{START.date()}/shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            json={"shadow_id": assignment.shadow_id, "sequence": 3},
        )
        corrupt_range = client.get(
            "/api/v1/paper/evidence/daily-shadow-comparison",
            headers={"X-Operator-Token": "operator"},
            params={
                "shadow_id": assignment.shadow_id,
                "start": START.date().isoformat(),
                "end": START.date().isoformat(),
            },
        )
    assert corrupt_response.status_code == 503
    assert corrupt_range.status_code == 503


def test_daily_comparison_rejects_unlinked_shadow_period(tmp_path):
    from test_daily_evidence import END, START, checkpoints
    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    bars = forward_bars(assignment)
    prior = observe_shadow(
        assignment.shadow_id, "prior-period", bars[:3], shadow_store, candidates, strategies
    )
    current = observe_shadow(
        assignment.shadow_id,
        "daily-period",
        bars[3:],
        shadow_store,
        candidates,
        strategies,
    )
    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(START.date(), opening, closing), END)
    daily_execution = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (), published_at=END
    )

    from ai_quant_os.paper_shadow_comparison import compare_daily_paper_to_shadow

    with pytest.raises(ValueError, match="same UTC day"):
        compare_daily_paper_to_shadow(daily_execution, prior, current)
