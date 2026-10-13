from datetime import timedelta, timezone
from decimal import Decimal

from ai_quant_os.market_data import Bar
from ai_quant_os.shadow_scheduler import ShadowScheduler
from ai_quant_os.world_state_store import WorldStateStore


def scheduler_fixture(tmp_path):
    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.shadow_pipeline import assign_shadow

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    bars = forward_bars(assignment)
    market_store = WorldStateStore(tmp_path / "paper.sqlite3")
    now = bars[-1].observed_at + timedelta(seconds=1)
    worker = ShadowScheduler(
        shadow_store,
        market_store,
        candidates,
        strategies,
        lambda: now.astimezone(timezone.utc),
        lambda: True,
    )
    return assignment, shadow_store, market_store, bars, worker


def test_scheduler_waits_for_minimum_initial_history_then_catches_up_idempotently(tmp_path):
    assignment, shadow_store, market_store, bars, worker = scheduler_fixture(tmp_path)
    market_store.record_bars(bars[:1])

    assert worker.step()["state"] == "WAITING"
    assert shadow_store.latest(assignment.shadow_id) is None

    market_store.record_bars(bars[1:])
    status = worker.step()
    report = shadow_store.latest(assignment.shadow_id)
    assert status["state"] == "WAITING"
    assert status["processed_batches"] == 1
    assert report is not None and report.source_bars == bars
    assert report.promotion_allowed is False
    assert worker.step()["processed_batches"] == 1


def test_scheduler_halts_before_reading_market_data_when_audit_fails(tmp_path):
    assignment, shadow_store, market_store, bars, worker = scheduler_fixture(tmp_path)
    market_store.record_bars(bars)
    worker.integrity = lambda: False

    status = worker.step()

    assert status["state"] == "ERROR"
    assert status["last_error"] == "PAPER_AUDIT_INTEGRITY_FAILED"
    assert shadow_store.latest(assignment.shadow_id) is None


def test_scheduler_stops_at_the_verified_shadow_source_limit(tmp_path):
    assignment, shadow_store, market_store, _, worker = scheduler_fixture(tmp_path)
    start = assignment.assigned_at + timedelta(seconds=1)
    bars = tuple(
        Bar(
            instrument_id="EURUSD",
            observed_at=start + timedelta(seconds=index),
            open=Decimal("1.1"),
            high=Decimal("1.1"),
            low=Decimal("1.1"),
            close=Decimal("1.1"),
            volume=Decimal("1"),
        )
        for index in range(4098)
    )
    market_store.record_bars(bars[:4096])
    market_store.record_bars(bars[4096:])
    worker.clock = lambda: bars[-1].observed_at + timedelta(seconds=1)

    first = worker.step()
    assert first["state"] == "WAITING"
    assert first["processed_batches"] == 1
    assert len(shadow_store.latest(assignment.shadow_id).source_bars) == 4096

    second = worker.step()
    assert second["state"] == "ERROR"
    assert second["last_error"] == "SHADOW_SOURCE_LIMIT_REACHED"
    assert second["processed_batches"] == 1
    assert len(shadow_store.latest(assignment.shadow_id).source_bars) == 4096
