from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import sqlite3

import pytest

from test_daily_evidence import DAY, END, START, checkpoints


def test_daily_execution_attribution_binds_to_published_daily_sources():
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.execution_attribution import DecisionPriceBenchmark
    from ai_quant_os.market import MarketSnapshot

    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
    owner = closing.owners[1]
    benchmark = DecisionPriceBenchmark(
        owner.order_id,
        owner.intent_id,
        "risk-sell",
        START + timedelta(hours=1),
        MarketSnapshot(
            "EURUSD",
            Decimal("1.19"),
            Decimal("1.21"),
            START + timedelta(minutes=59),
            "world",
            True,
        ),
    )

    evidence = build_daily_execution_evidence(
        daily,
        closing.owners,
        closing.fills,
        (benchmark,),
        published_at=END,
    )

    assert evidence.daily.report.instruments[0].daily_pnl_quote == Decimal("1.4")
    assert evidence.report.contribution.coverage == "COMPLETE"
    assert evidence.report.contribution.instruments[0].fill_count == 1
    assert evidence.report.contribution.instruments[0].actual_cash_flow_quote == Decimal("4.6")
    assert evidence.report.contribution.instruments[0].reference_mid_cash_flow_quote == Decimal(
        "4.8"
    )
    assert evidence.report.contribution.instruments[0].spread_cash_adjustment_quote == Decimal(
        "-0.04"
    )
    assert evidence.report.contribution.instruments[
        0
    ].decision_to_fill_cash_adjustment_quote == Decimal("0.04")


def test_daily_execution_attribution_rejects_source_changes_and_lookahead():
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.execution_attribution import DecisionPriceBenchmark
    from ai_quant_os.market import MarketSnapshot

    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
    owner = closing.owners[1]
    fill = closing.fills[1]
    benchmark = DecisionPriceBenchmark(
        owner.order_id,
        owner.intent_id,
        "risk-sell",
        START + timedelta(hours=1),
        MarketSnapshot(
            "EURUSD",
            Decimal("1.19"),
            Decimal("1.21"),
            START + timedelta(minutes=59),
            "world",
            True,
        ),
    )

    with pytest.raises(ValueError, match="daily source"):
        build_daily_execution_evidence(
            daily,
            closing.owners,
            (replace(fill, price=Decimal("1.3")),),
            (benchmark,),
            published_at=END,
        )

    late = replace(benchmark, decision_at=fill.observed_at + timedelta(seconds=1))
    with pytest.raises(ValueError):
        build_daily_execution_evidence(
            daily, closing.owners, closing.fills, (late,), published_at=END
        )


def test_daily_execution_attribution_withholds_missing_benchmarks_and_late_history():
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.portfolio import PaperFill

    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
    result = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (), published_at=END
    )
    assert result.report.contribution.coverage == "NONE"
    assert result.report.contribution.instruments[0].actual_cash_flow_quote == Decimal("4.6")
    assert result.report.contribution.instruments[0].reference_mid_cash_flow_quote is None

    late_historical_fill = PaperFill(
        "late-history",
        "buy",
        "EURUSD",
        "BUY",
        Decimal(1),
        Decimal("1.1"),
        Decimal(0),
        START - timedelta(minutes=30),
    )
    with pytest.raises(ValueError, match="execution history"):
        build_daily_execution_evidence(
            daily,
            closing.owners,
            (*closing.fills, late_historical_fill),
            (),
            published_at=END,
        )


def test_daily_execution_evidence_is_immutable_source_bound_and_replays(tmp_path):
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.execution_attribution import DecisionPriceBenchmark
    from ai_quant_os.evidence_store import EvidenceStore
    from ai_quant_os.market import MarketSnapshot

    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
    owner = closing.owners[1]
    benchmark = DecisionPriceBenchmark(
        owner.order_id,
        owner.intent_id,
        "risk-sell",
        START + timedelta(hours=1),
        MarketSnapshot(
            "EURUSD",
            Decimal("1.19"),
            Decimal("1.21"),
            START + timedelta(minutes=59),
            "world",
            True,
        ),
    )
    value = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (benchmark,), published_at=END
    )
    archive = EvidenceStore(tmp_path / "evidence.sqlite3")
    archive.checkpoint(opening)
    archive.checkpoint(closing)
    archive.publish(daily)

    assert archive.publish_daily_execution(value) == value
    restarted = EvidenceStore(tmp_path / "evidence.sqlite3")
    assert restarted.daily_execution(DAY) == value
    assert restarted.publish_daily_execution(value) == value
    assert (
        restarted.publish_daily_execution(replace(value, published_at=END + timedelta(seconds=1)))
        == value
    )

    altered_benchmark = replace(benchmark, market=replace(benchmark.market, bid=Decimal("1.18")))
    changed = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (altered_benchmark,), published_at=END
    )
    with pytest.raises(ValueError, match="cannot change"):
        restarted.publish_daily_execution(changed)

    with sqlite3.connect(restarted.path) as db:
        db.execute("UPDATE daily_execution_evidence SET blob=?", (b"{}",))
    with pytest.raises(ValueError, match="fingerprint"):
        restarted.daily_execution(DAY)
