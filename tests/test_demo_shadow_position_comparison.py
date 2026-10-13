from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from test_mt5_account_sync import NOW
from test_mt5_sync_controller import setup
from test_shadow_portfolio import forward_bars, shadow_fixture


def test_demo_shadow_position_comparison_reports_direction_without_mixing_units(
    tmp_path,
):
    from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.demo_shadow_position_comparison import compare_demo_shadow_position

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    bars = tuple(
        replace(bar, observed_at=NOW - timedelta(minutes=len(bars) - index - 1))
        for index, bar in enumerate(bars)
    )
    report = observe_shadow(
        assignment.shadow_id,
        "broker-comparison",
        bars,
        store,
        candidates,
        strategies,
    )
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    portfolio = build_portfolio_publication(
        controller, "84f8730b-d1bd-45e2-9fb5-4d89f6324dcc", clock=lambda: NOW
    )
    comparison = compare_demo_shadow_position(portfolio.record, report)

    assert comparison.mode == "DEMO_OBSERVATION"
    assert comparison.shadow_id == assignment.shadow_id
    assert comparison.candidate_id == assignment.candidate.manifest.candidate_id
    assert comparison.instrument_id == "EURUSD"
    assert comparison.state == "DEMO_ONLY"
    assert comparison.shadow_position_strategy_units == report.run.result.final_position
    assert comparison.demo_position_current_contract_units == Decimal("2000")
    assert comparison.magnitude_comparison == "NOT_AVAILABLE"
    assert not hasattr(comparison, "difference_quantity_units")
    assert comparison.demo_source_receipt_ref == portfolio.record.source_receipt_ref
    assert comparison.shadow_source_sha256 == report.fingerprint
    assert comparison.demo_quantity_basis == "CURRENT_SYMBOL_METADATA"
    assert comparison.shadow_quantity_basis == "STRATEGY_MODEL_UNITS"
    assert comparison.causal_attribution is False
    assert comparison.promotion_allowed is False
    assert comparison.may_submit_orders is False


def test_demo_shadow_position_comparison_rejects_failed_shadow(tmp_path):
    from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.demo_shadow_position_comparison import compare_demo_shadow_position

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "broker-comparison",
        forward_bars(assignment),
        store,
        candidates,
        strategies,
    )
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    portfolio = build_portfolio_publication(
        controller, "84f8730b-d1bd-45e2-9fb5-4d89f6324dcc", clock=lambda: NOW
    )
    changed = replace(report, outcome="FAILED", run=None, reason="NATIVE_EXECUTION_FAILURE")

    import pytest

    with pytest.raises(ValueError, match="observed shadow"):
        compare_demo_shadow_position(portfolio.record, changed)


def test_demo_shadow_position_comparison_distinguishes_hedged_neutral_from_flat(tmp_path):
    from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.demo_shadow_position_comparison import compare_demo_shadow_position

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    bars = tuple(
        replace(bar, observed_at=NOW - timedelta(minutes=len(bars) - index - 1))
        for index, bar in enumerate(bars)
    )
    report = observe_shadow(
        assignment.shadow_id,
        "broker-comparison",
        bars,
        store,
        candidates,
        strategies,
    )
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    portfolio = build_portfolio_publication(
        controller, "84f8730b-d1bd-45e2-9fb5-4d89f6324dcc", clock=lambda: NOW
    )
    buy = portfolio.record.positions[0]
    sell = buy.model_copy(
        update={"ticket": buy.ticket + 1, "identifier": buy.identifier + 1, "side": "SELL"}
    )
    hedged_demo = portfolio.record.model_copy(update={"positions": (buy, sell)})

    comparison = compare_demo_shadow_position(hedged_demo, report)

    assert comparison.state == "DEMO_HEDGED_NEUTRAL"
    assert comparison.demo_position_current_contract_units == 0
    assert comparison.demo_has_instrument_positions is True


def test_demo_shadow_comparison_withholds_when_history_starts_inside_shadow_interval(tmp_path):
    from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.demo_shadow_position_comparison import compare_demo_shadow_position

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    bars = tuple(
        replace(bar, observed_at=NOW - timedelta(minutes=len(bars) - index - 1))
        for index, bar in enumerate(bars)
    )
    report = observe_shadow(
        assignment.shadow_id,
        "broker-comparison",
        bars,
        store,
        candidates,
        strategies,
    )
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    portfolio = build_portfolio_publication(
        controller, "84f8730b-d1bd-45e2-9fb5-4d89f6324dcc", clock=lambda: NOW
    )
    incomplete_demo = portfolio.record.model_copy(
        update={"history_start": bars[0].observed_at + timedelta(seconds=1)}
    )

    comparison = compare_demo_shadow_position(incomplete_demo, report)

    assert comparison.state == "WITHHELD"
    assert comparison.code == "HISTORY_WINDOW_MISMATCH"
    assert comparison.demo_position_current_contract_units is None
    assert comparison.shadow_position_strategy_units is None


def test_protected_demo_shadow_endpoint_withholds_noncontemporaneous_sources(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
    from ai_quant_os.mt5_sync_service import publish_portfolio
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    observe_shadow(
        assignment.shadow_id,
        "broker-comparison",
        forward_bars(assignment),
        store,
        candidates,
        strategies,
    )
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    portfolio = build_portfolio_publication(
        controller, "84f8730b-d1bd-45e2-9fb5-4d89f6324dcc", clock=lambda: NOW
    )
    publish_portfolio(demo_dir / "portfolio.json", portfolio)
    path = f"/api/v1/paper/shadows/{assignment.shadow_id}/demo-position-comparison?sequence=1"

    with TestClient(
        create_app(
            operator_token="operator",
            research_workspace=tmp_path,
            demo_sync_state_dir=demo_dir,
            clock=lambda: NOW,
        )
    ) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-Operator-Token": "operator"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["state"] == "WITHHELD"
    assert body["code"] == "SOURCE_TIME_MISMATCH"
    assert body["demo_position_current_contract_units"] is None
    assert body["shadow_position_strategy_units"] is None
    assert body["demo_has_instrument_positions"] is True
    assert body["magnitude_comparison"] == "NOT_AVAILABLE"
    assert body["demo_attestation"] == "UNVERIFIED"
    assert body["causal_attribution"] is False
    assert body["promotion_allowed"] is False
    assert body["may_submit_orders"] is False


def test_demo_shadow_comparison_schema_matches_runtime_contract():
    import json
    from pathlib import Path
    from pydantic import TypeAdapter

    from ai_quant_os.demo_shadow_position_comparison import DemoShadowPositionComparison

    schema_path = Path("docs/schemas/demo-shadow-position-comparison-v1.schema.json")
    assert schema_path.is_file()
    assert (
        json.loads(schema_path.read_text(encoding="utf-8"))
        == TypeAdapter(DemoShadowPositionComparison).json_schema()
    )
