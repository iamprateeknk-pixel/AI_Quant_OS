from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from test_daily_evidence import DAY, END, START, checkpoints


def test_period_aggregation_reconciles_source_daily_reports_and_missing_days():
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.daily_execution_aggregation import aggregate_daily_execution
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
            "EURUSD", Decimal("1.19"), Decimal("1.21"), START + timedelta(minutes=59), "world", True
        ),
    )
    evidence = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (benchmark,), published_at=END
    )

    from ai_quant_os.daily_evidence import EvidenceCheckpoint, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.execution_attribution import DecisionPriceBenchmark
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.portfolio import PaperFill
    from ai_quant_os.currency_portfolio import CurrencyInstrument

    second_owner = replace(
        owner,
        order_id="sell-two",
        intent_id="sell-two-intent",
        quantity=Decimal(2),
        created_at=END + timedelta(hours=1),
        expires_at=END + timedelta(hours=4),
        intent_sha256="e" * 64,
        order_sha256="f" * 64,
    )
    second_fill = PaperFill(
        "during-two",
        second_owner.order_id,
        "EURUSD",
        "SELL",
        Decimal(2),
        Decimal("1.25"),
        Decimal("0.1"),
        END + timedelta(hours=2),
    )
    second_catalog = (CurrencyInstrument("EURUSD", "EUR", "CAD"),)
    second_open = replace(closing, catalog=second_catalog)
    second_close = EvidenceCheckpoint(
        "second-close",
        END + timedelta(days=1),
        second_open.owners + (second_owner,),
        second_open.fills + (second_fill,),
        (
            MarketSnapshot(
                "EURUSD",
                Decimal("1.3"),
                Decimal("1.31"),
                END + timedelta(days=1),
                "second-close",
                True,
            ),
        ),
        second_catalog,
    )
    second_day = DAY + timedelta(days=1)
    second_daily = DailyEvidence(
        second_open,
        second_close,
        build_daily_report(second_day, second_open, second_close),
        END + timedelta(days=1),
    )
    second_benchmark = DecisionPriceBenchmark(
        second_owner.order_id,
        second_owner.intent_id,
        "risk-sell-two",
        END + timedelta(hours=1, minutes=30),
        MarketSnapshot(
            "EURUSD", Decimal("1.24"), Decimal("1.26"), END + timedelta(hours=1), "world-two", True
        ),
    )
    second_evidence = build_daily_execution_evidence(
        second_daily,
        second_close.owners,
        second_close.fills,
        (second_benchmark,),
        published_at=END + timedelta(days=1),
    )
    report = aggregate_daily_execution(DAY, second_day, (evidence, second_evidence))

    assert report.covered_days == (DAY, second_day)
    assert report.missing_days == ()
    assert len(report.sources) == 2
    beta_usd = next(
        row
        for row in report.strategies
        if row.strategy_id == "beta" and row.quote_currency == "USD"
    )
    beta_cad = next(
        row
        for row in report.strategies
        if row.strategy_id == "beta" and row.quote_currency == "CAD"
    )
    assert beta_usd.actual_cash_flow_quote == Decimal("4.60")
    assert beta_usd.reference_mid_cash_flow_quote == Decimal("4.80")
    assert beta_usd.spread_cash_adjustment_quote == Decimal("-0.04")
    assert beta_usd.decision_to_fill_cash_adjustment_quote == Decimal("0.04")
    assert beta_cad.actual_cash_flow_quote == Decimal("2.40")
    assert beta_cad.reference_mid_cash_flow_quote == Decimal("2.50")
    assert beta_cad.spread_cash_adjustment_quote == Decimal("-0.02")
    assert beta_cad.decision_to_fill_cash_adjustment_quote == Decimal("0.02")
    assert beta_usd.fill_count == beta_usd.benchmark_fill_count == 1
    assert beta_cad.fill_count == beta_cad.benchmark_fill_count == 1
    assert {
        (row.strategy_id, row.strategy_version, row.instrument_id, row.quote_currency)
        for row in report.strategies
    } == {("beta", "1", "EURUSD", "USD"), ("beta", "1", "EURUSD", "CAD")}
    assert report.promotion_allowed is False


def test_period_aggregation_preserves_missing_benchmarks_and_rejects_bad_windows():
    from ai_quant_os.daily_evidence import DailyEvidence, build_daily_report
    from ai_quant_os.daily_execution_evidence import build_daily_execution_evidence
    from ai_quant_os.daily_execution_aggregation import aggregate_daily_execution

    opening, closing = checkpoints()
    daily = DailyEvidence(opening, closing, build_daily_report(DAY, opening, closing), END)
    evidence = build_daily_execution_evidence(
        daily, closing.owners, closing.fills, (), published_at=END
    )

    report = aggregate_daily_execution(DAY, DAY, (evidence,))
    assert report.strategies[0].reference_mid_cash_flow_quote is None
    assert report.strategies[0].actual_cash_flow_quote == Decimal("4.6")
    assert report.strategies[0].benchmark_fill_count == 0
    with pytest.raises(ValueError):
        aggregate_daily_execution(DAY, DAY - timedelta(days=1), (evidence,))
    with pytest.raises(ValueError):
        aggregate_daily_execution(DAY - timedelta(days=200), DAY + timedelta(days=200), ())
    with pytest.raises(ValueError):
        aggregate_daily_execution(DAY, DAY, (evidence, evidence))
    empty = aggregate_daily_execution(DAY, DAY, ())
    assert empty.coverage == "NO_EVIDENCE"
    assert empty.covered_days == ()
    assert empty.missing_days == (DAY,)
