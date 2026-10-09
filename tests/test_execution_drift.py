import importlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from ai_quant_os.execution_attribution import DecisionPriceBenchmark
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.portfolio import PaperFill

NOW = datetime(2026, 1, 1, 3, tzinfo=timezone.utc)


def module():
    assert importlib.util.find_spec("ai_quant_os.execution_drift"), (
        "Missing A14 execution drift comparison"
    )
    return importlib.import_module("ai_quant_os.execution_drift")


def sources():
    owners, fills, benchmarks = [], [], []
    for i, minutes in enumerate((100, 90, 40, 30)):
        stamp = NOW - timedelta(minutes=minutes)
        key = f"order-{i}"
        market = MarketSnapshot(
            "EURUSD",
            Decimal("1"),
            Decimal("1.0002"),
            stamp - timedelta(seconds=1),
            "fixed-world",
            True,
        )
        owners.append(
            OrderAttribution(
                key,
                key,
                "alpha",
                "1.0.0",
                "EURUSD",
                "BUY",
                Decimal(10),
                "ACCEPTED",
                stamp - timedelta(seconds=2),
                stamp + timedelta(seconds=2),
                "a" * 64,
                "b" * 64,
            )
        )
        fills.append(
            PaperFill(
                f"fill-{i}",
                key,
                "EURUSD",
                "BUY",
                Decimal(10),
                Decimal("1.0002") if i < 2 else Decimal("1.0022"),
                Decimal("0.001"),
                stamp,
            )
        )
        benchmarks.append(
            DecisionPriceBenchmark(key, key, f"risk-{i}", stamp - timedelta(seconds=1), market)
        )
    return tuple(owners), tuple(fills), tuple(benchmarks)


def test_declared_adjacent_windows_detect_normalized_execution_cost_increase():
    drift = module()
    policy = drift.DriftPolicy(3600, 2, Decimal(10))
    result = drift.compare_execution_drift(*sources(), now=NOW, policy=policy)
    row = result.assessments[0]
    assert row.state == "COST_INCREASE"
    assert row.cost_delta_bps == Decimal("19.998000")
    assert row.slippage_delta_bps == row.cost_delta_bps
    assert row.spread_delta_bps == row.fee_delta_bps == 0
    assert row.baseline.fill_count == row.current.fill_count == 2
    assert result.current_start == NOW - timedelta(hours=1)
    assert result.baseline_start == NOW - timedelta(hours=2)
    assert row.recommendation == "HUMAN_REVIEW_ONLY"
    with localcontext() as context:
        context.prec = 4
        assert drift.compare_execution_drift(*sources(), now=NOW, policy=policy) == result


def test_missing_benchmarks_and_small_samples_never_infer_no_drift():
    drift = module()
    policy = drift.DriftPolicy(3600, 2, Decimal(10))
    owners, fills, benchmarks = sources()
    result = drift.compare_execution_drift(owners, fills, benchmarks[:-1], now=NOW, policy=policy)
    row = result.assessments[0]
    assert row.state == "UNASSESSED" and row.cost_delta_bps is None
    assert row.current.cost_bps is None
    assert "MISSING_BENCHMARKS" in row.reasons
    result = drift.compare_execution_drift(owners, fills[1:], benchmarks, now=NOW, policy=policy)
    assert result.assessments[0].state == "UNASSESSED"
    assert "INSUFFICIENT_BASELINE" in result.assessments[0].reasons


def test_no_policy_is_explicit_and_forged_sources_are_refused():
    drift = module()
    owners, fills, benchmarks = sources()
    assert (
        drift.compare_execution_drift(owners, fills, benchmarks, now=NOW, policy=None).state
        == "POLICY_UNCONFIGURED"
    )
    with pytest.raises(ValueError):
        drift.compare_execution_drift(owners, fills + (fills[0],), benchmarks, now=NOW, policy=None)
    with pytest.raises(ValueError):
        drift.compare_execution_drift(
            owners,
            fills,
            (replace(benchmarks[0], intent_id="foreign"),) + benchmarks[1:],
            now=NOW,
            policy=None,
        )


def test_sell_cost_sign_and_changed_notional_do_not_create_raw_cash_pnl_alerts():
    drift = module()
    declared = drift.DriftPolicy(3600, 2, Decimal(10))
    owners, fills, benchmarks = sources()
    sell = tuple(replace(row, side="SELL") for row in owners)
    sold = tuple(
        replace(row, side="SELL", price=Decimal(1) if i < 2 else Decimal("0.998"))
        for i, row in enumerate(fills)
    )
    assert drift.compare_execution_drift(
        sell, sold, benchmarks, now=NOW, policy=declared
    ).assessments[0].cost_delta_bps == Decimal("19.998000")
    owners = tuple(
        replace(row, quantity=Decimal(100) if i >= 2 else row.quantity)
        for i, row in enumerate(owners)
    )
    fills = tuple(
        replace(
            row,
            quantity=Decimal(100) if i >= 2 else row.quantity,
            price=Decimal("1.0002"),
            fee=Decimal("0.01") if i >= 2 else row.fee,
        )
        for i, row in enumerate(fills)
    )
    row = drift.compare_execution_drift(
        owners, fills, benchmarks, now=NOW, policy=declared
    ).assessments[0]
    assert row.state == "NO_COST_INCREASE" and row.cost_delta_bps == 0
    assert (
        row.current.initial_mid_gross_notional_quote
        == 10 * row.baseline.initial_mid_gross_notional_quote
    )


def test_versions_do_not_pool_samples_and_utc_boundary_windows_do_not_overlap():
    drift = module()
    owners, fills, benchmarks = sources()
    declared = drift.DriftPolicy(3600, 2, Decimal(10))
    separated = tuple(
        replace(row, strategy_version="2.0.0" if i % 2 else "1.0.0") for i, row in enumerate(owners)
    )
    rows = drift.compare_execution_drift(
        separated, fills, benchmarks, now=NOW, policy=declared
    ).assessments
    assert len(rows) == 2 and all(row.state == "UNASSESSED" for row in rows)
    times = (
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1, seconds=1),
        NOW - timedelta(hours=1),
        NOW,
    )
    owners = tuple(
        replace(
            row, created_at=stamp - timedelta(seconds=2), expires_at=stamp + timedelta(seconds=2)
        )
        for row, stamp in zip(owners, times)
    )
    fills = tuple(replace(row, observed_at=stamp) for row, stamp in zip(fills, times))
    benchmarks = tuple(
        replace(
            row,
            decision_at=stamp - timedelta(seconds=1),
            market=replace(row.market, observed_at=stamp - timedelta(seconds=1)),
        )
        for row, stamp in zip(benchmarks, times)
    )
    row = drift.compare_execution_drift(
        owners, fills, benchmarks, now=NOW, policy=declared
    ).assessments[0]
    assert row.baseline.fill_count == row.current.fill_count == 2


@pytest.mark.parametrize(
    "window,minimum,threshold",
    [
        (True, 2, Decimal(1)),
        (60, True, Decimal(1)),
        (59, 2, Decimal(1)),
        (60, 1, Decimal(1)),
        (60, 2, Decimal("NaN")),
        (60, 2, Decimal("0.0000001")),
        (60, 2, 1),
        (60, 2, Decimal(-1)),
    ],
)
def test_policy_refuses_ambiguous_or_unbounded_limits(window, minimum, threshold):
    with pytest.raises(ValueError):
        module().DriftPolicy(window, minimum, threshold)


def test_retained_development_snapshot_recomputes_without_claiming_calibration():
    import json
    from pathlib import Path
    from pydantic import TypeAdapter
    from ai_quant_os.execution_drift import (
        DriftPolicy,
        ExecutionDriftReport,
        compare_execution_drift,
    )

    root = Path(__file__).parents[1]
    saved = json.loads(
        (root / "docs/evaluations/drift-development-baseline.json").read_text(encoding="utf-8")
    )
    owners = TypeAdapter(tuple[OrderAttribution, ...]).validate_python(saved["owners"])
    fills = TypeAdapter(tuple[PaperFill, ...]).validate_python(saved["fills"])
    benchmarks = TypeAdapter(tuple[DecisionPriceBenchmark, ...]).validate_python(
        saved["benchmarks"]
    )
    declared = TypeAdapter(DriftPolicy).validate_python(saved["policy"])
    report = compare_execution_drift(
        owners, fills, benchmarks, now=datetime.fromisoformat(saved["as_of"]), policy=declared
    )
    assert TypeAdapter(ExecutionDriftReport).dump_python(report, mode="json") == saved["report"]
    assert saved["calibration_status"] == "UNAVAILABLE" and saved["promotion_approved"] is False
