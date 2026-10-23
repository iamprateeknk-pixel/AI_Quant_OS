from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("nautilus_trader")


def shadow_fixture(tmp_path):
    from test_research_candidates import archived_workspace
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle
    from ai_quant_os.shadow_portfolio import ShadowAssignment
    from ai_quant_os.shadow_store import ShadowStore

    report, research, strategies = archived_workspace(tmp_path)
    candidate = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    candidates = CandidateStore(tmp_path / "candidates.sqlite3")
    view = candidates.register(candidate)
    cutoff = report.plan.holdout_scope.last_observed_at + timedelta(minutes=1)
    assignment = ShadowAssignment("shadow-fixture", cutoff, view, candidate)
    store = ShadowStore(tmp_path / "shadow.sqlite3")
    return assignment, store, candidates, strategies


def forward_bars(assignment):
    from ai_quant_os.market_data import Bar

    return tuple(
        Bar(
            "EURUSD",
            assignment.assigned_at + timedelta(minutes=i + 1),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for i, price in enumerate(("1.1", "1.2", "1.3", "1.2", "1.1", "1.2"))
    )


def test_shadow_native_portfolio_accumulates_causal_prefixes_without_broker(tmp_path, monkeypatch):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.broker import SimulatedBroker

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Shadow portfolio cannot call broker")

    monkeypatch.setattr(SimulatedBroker, "place", forbidden)
    assert assign_shadow(assignment, store, candidates, strategies) == assignment
    bars = forward_bars(assignment)
    first = observe_shadow("shadow-fixture", "batch-one", bars[:3], store, candidates, strategies)
    second = observe_shadow("shadow-fixture", "batch-two", bars[3:], store, candidates, strategies)
    assert first.outcome == second.outcome == "OBSERVED"
    assert first.promotion_allowed is second.promotion_allowed is False
    assert second.sequence == 2
    assert second.source_bars == bars
    assert second.run.result.fills[: len(first.run.result.fills)] == first.run.result.fills
    assert second.previous_sha256 == first.fingerprint
    assert store.latest("shadow-fixture") == second
    assert second.run.binding == assignment.candidate.report.holdout.binding
    original = candidates.get(assignment.candidate.manifest.candidate_id)
    assert original == assignment.candidate_view


def test_selector_shadow_performance_uses_latest_verified_candidate_report(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.strategy_selector_sources import shadow_performance_observation

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    before = shadow_performance_observation(store, now=assignment.assigned_at)
    assert before.availability == "NO_OBSERVATIONS"
    assert before.rows[0].outcome == "NOT_OBSERVED"

    bars = forward_bars(assignment)
    observe_shadow(assignment.shadow_id, "batch-one", bars, store, candidates, strategies)
    report = store.latest(assignment.shadow_id)
    after = shadow_performance_observation(store, now=report.observed_at)
    assert after.availability == "AVAILABLE"
    assert len(after.rows) == 1
    row = after.rows[0]
    assert row.candidate_id == assignment.candidate.manifest.candidate_id
    assert row.shadow_id == assignment.shadow_id
    assert row.sequence == report.sequence
    assert row.outcome == report.outcome == "OBSERVED"
    assert row.net_pnl == report.run.result.net_pnl
    assert row.pnl_currency == report.run.result.pnl_currency
    assert row.report_sha256 == report.fingerprint


def test_selector_v3_receives_verified_shadow_metrics_but_never_ranks_them(tmp_path, monkeypatch):
    import asyncio
    import json
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import ToolInvocation, load_agent_catalog
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.strategy_selector_agent import selector_implementation_v3
    from ai_quant_os.strategy_selector_service import StrategyRegistryRead
    from ai_quant_os.strategy_selector_sources import selector_sources_v3
    from ai_quant_os.world_state import WorldStateEngine

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, shadow_store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id,
        "batch-one",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    now = report.observed_at
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=now)
    registry = load_agent_catalog()
    sources = selector_sources_v3(
        candidates.list(),
        snapshot,
        now=now,
        registry_available=True,
        shadow_store=shadow_store,
    )
    context = asyncio.run(
        ContextCompiler(
            registry,
            sources,
            clock=lambda: now + timedelta(microseconds=1),
            timeout_seconds=2,
        ).compile("A12", "0.3.0", task_id="selector-shadow", scope="PAPER")
    )
    performance = json.loads(context.inputs[2].payload_json)
    assert performance["availability"] == "AVAILABLE"
    assert performance["rows"][0]["candidate_id"] == assignment.candidate.manifest.candidate_id
    assert performance["rows"][0]["report_sha256"] == report.fingerprint

    class Tools:
        async def call(self, tool, payload):
            assert tool == "STRATEGY_REGISTRY_READ"
            return {
                "registry_sha256": payload["registry_sha256"],
                "candidate_count": len(candidates.list()),
                "performance_sha256": payload["performance_sha256"],
                "status": "CHECKED_REGISTRY_AND_SHADOW_SNAPSHOT",
            }

    output = asyncio.run(
        selector_implementation_v3(registry).invoke(AgentInvocation(context, Tools()))
    )
    output.require_context(context)
    assert output.eligible_strategies == ()
    assert "CANDIDATE_PERFORMANCE_NOT_QUALIFIED" in output.blockers
    assert output.performance_available is False
    assert output.promotion_allowed is False

    from ai_quant_os.strategy_selector_sources import ShadowPerformanceObservation

    payload = {
        "registry_sha256": json.loads(context.inputs[0].payload_json)["registry_sha256"],
        "performance_sha256": ShadowPerformanceObservation.model_validate_json(
            context.inputs[2].payload_json
        ).fingerprint,
    }
    service = StrategyRegistryRead(
        lambda: candidates,
        registry,
        context=lambda context_ref: context,
        clock=lambda: context.created_at + timedelta(microseconds=1),
        shadow_store=lambda: shadow_store,
    )
    invocation = ToolInvocation(
        "A12",
        "0.3.0",
        "STRATEGY_REGISTRY_READ",
        "PAPER",
        payload,
        context.context_ref,
        context.task_id,
    )
    receipt = asyncio.run(service(invocation))
    assert receipt["status"] == "CHECKED_REGISTRY_AND_SHADOW_SNAPSHOT"

    import threading
    import ai_quant_os.strategy_selector_sources as selector_sources_module

    scan_started = threading.Event()
    scan_finished = threading.Event()
    release_scan = threading.Event()
    original_scan = selector_sources_module.shadow_performance_observation

    def slow_scan(store, *, now):
        scan_started.set()
        release_scan.wait(1)
        try:
            return original_scan(store, now=now)
        finally:
            scan_finished.set()

    monkeypatch.setattr(selector_sources_module, "shadow_performance_observation", slow_scan)

    async def check_deadline_and_responsiveness():
        ticks = 0

        async def keep_loop_responsive():
            nonlocal ticks
            for _ in range(20):
                ticks += 1
                await asyncio.sleep(0)

        timed_call = asyncio.create_task(asyncio.wait_for(service(invocation), timeout=0.05))
        assert await asyncio.to_thread(scan_started.wait, 3)
        assert not scan_finished.is_set()
        await keep_loop_responsive()
        assert ticks == 20
        assert not scan_finished.is_set()
        release_scan.set()
        with pytest.raises(TimeoutError):
            await timed_call

        await asyncio.to_thread(scan_finished.wait, 3)
        assert scan_finished.is_set()

    asyncio.run(check_deadline_and_responsiveness())

    from ai_quant_os.market_data import Bar

    later = tuple(
        Bar(
            "EURUSD",
            assignment.assigned_at + timedelta(minutes=i),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(100),
        )
        for i, price in enumerate(("1.25", "1.3", "1.35"), start=7)
    )
    observe_shadow(
        assignment.shadow_id,
        "batch-two",
        later,
        shadow_store,
        candidates,
        strategies,
    )
    with pytest.raises(ValueError, match="Shadow performance changed"):
        asyncio.run(service(invocation))


def test_shadow_cached_restart_and_changed_command_refusal(tmp_path, monkeypatch):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_store import ShadowStore

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    first = observe_shadow(assignment.shadow_id, "batch-one", bars, store, candidates, strategies)

    def forbidden(*args, **kwargs):
        raise AssertionError("Cached shadow replay cannot execute")

    monkeypatch.setattr("ai_quant_os.shadow_pipeline.run_registered_backtest", forbidden)
    restarted = ShadowStore(store.path)
    assert (
        observe_shadow(assignment.shadow_id, "batch-one", bars, restarted, candidates, strategies)
        == first
    )
    with pytest.raises(ValueError, match="Immutable"):
        observe_shadow(
            assignment.shadow_id, "batch-one", bars[:-1], restarted, candidates, strategies
        )
    with pytest.raises(ValueError):
        observe_shadow(
            assignment.shadow_id, "reuse-period", bars, restarted, candidates, strategies
        )


def test_shadow_store_returns_a_verified_history_for_period_comparison(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_store import ShadowStore

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    first = observe_shadow(
        assignment.shadow_id, "batch-one", bars[:3], store, candidates, strategies
    )
    second = observe_shadow(
        assignment.shadow_id, "batch-two", bars[3:], store, candidates, strategies
    )

    assert ShadowStore(store.path).history(assignment.shadow_id) == (first, second)
    assert ShadowStore(store.path).assigned_ids() == (assignment.shadow_id,)


def test_shadow_assignment_listing_refuses_corrupt_or_oversized_inventory(tmp_path):
    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    from ai_quant_os.shadow_pipeline import assign_shadow

    assign_shadow(assignment, store, candidates, strategies)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE shadow_assignments SET blob=? WHERE shadow_id=?",
            (b"corrupt", assignment.shadow_id),
        )
    with pytest.raises(ValueError, match="assignment|hash|identity|integrity|fingerprint"):
        store.assigned_ids()


def test_shadow_assignment_listing_is_bounded(tmp_path):
    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    from ai_quant_os.shadow_pipeline import assign_shadow

    assign_shadow(assignment, store, candidates, strategies)
    with pytest.raises(ValueError, match="inventory limit"):
        store.assigned_ids(limit=0)


def test_shadow_retirement_is_durable_idempotent_and_blocks_new_observations(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_store import ShadowStore

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    report = observe_shadow(assignment.shadow_id, "batch-one", bars, store, candidates, strategies)
    retired_at = datetime.now(timezone.utc)

    retirement = store.retire(
        assignment.shadow_id,
        "retire-complete",
        "Diagnostic window complete",
        retired_at=retired_at,
    )

    restarted = ShadowStore(store.path)
    assert restarted.retirement(assignment.shadow_id) == retirement
    assert retirement.final_sequence == report.sequence
    assert retirement.final_sha256 == report.fingerprint
    assert (
        restarted.retire(
            assignment.shadow_id,
            "retire-complete",
            "Diagnostic window complete",
            retired_at=datetime.now(timezone.utc),
        )
        == retirement
    )
    assert (
        observe_shadow(assignment.shadow_id, "batch-one", bars, restarted, candidates, strategies)
        == report
    )
    with pytest.raises(ValueError, match="retired"):
        observe_shadow(
            assignment.shadow_id,
            "after-retirement",
            (forward_bars(assignment)[-1],),
            restarted,
            candidates,
            strategies,
        )
    with pytest.raises(ValueError, match="retirement command"):
        restarted.retire(
            assignment.shadow_id,
            "other-retirement",
            "Different command",
            retired_at=datetime.now(timezone.utc),
        )


def test_shadow_retirement_refuses_uncertain_reservations(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow
    from ai_quant_os.shadow_portfolio import ShadowRequest

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    store.reserve(
        ShadowRequest(assignment.shadow_id, "pending-observation", forward_bars(assignment))
    )

    with pytest.raises(ValueError, match="reservation blocks retirement"):
        store.retire(
            assignment.shadow_id,
            "retire-pending",
            "Close incomplete diagnostic",
            retired_at=datetime.now(timezone.utc),
        )


def test_shadow_uncertain_reservation_requires_audited_explicit_recovery(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_portfolio import ShadowRequest
    from ai_quant_os.shadow_store import ShadowStore

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    request = ShadowRequest(assignment.shadow_id, "interrupted-batch", bars)
    stale_token, *_ = store.reserve(request)
    with pytest.raises(ValueError, match="supplied together"):
        observe_shadow(
            assignment.shadow_id,
            "interrupted-batch",
            bars,
            store,
            candidates,
            strategies,
            recovery_id="recover-interrupted-batch",
        )
    with pytest.raises(ValueError, match="requires investigation"):
        observe_shadow(
            assignment.shadow_id,
            "interrupted-batch",
            bars,
            store,
            candidates,
            strategies,
        )

    report = observe_shadow(
        assignment.shadow_id,
        "interrupted-batch",
        bars,
        store,
        candidates,
        strategies,
        recovery_id="recover-interrupted-batch",
        recovery_reason="Confirmed prior local worker stopped",
    )

    assert report.outcome == "OBSERVED"
    assert len(store.recoveries(assignment.shadow_id)) == 1
    recovery = store.recoveries(assignment.shadow_id)[0]
    assert recovery.command_id == request.command_id
    from hashlib import sha256
    from ai_quant_os.research_store import encode_research

    assert recovery.request_sha256 == sha256(encode_research(request)).hexdigest()
    assert recovery.recovery_id == "recover-interrupted-batch"
    with pytest.raises(ValueError, match="ownership"):
        store.finish(stale_token, report)
    restarted = ShadowStore(store.path)
    assert (
        observe_shadow(
            assignment.shadow_id,
            "interrupted-batch",
            bars,
            restarted,
            candidates,
            strategies,
            recovery_id="recover-interrupted-batch",
            recovery_reason="Confirmed prior local worker stopped",
        )
        == report
    )
    assert len(restarted.recoveries(assignment.shadow_id)) == 1


def test_shadow_refuses_holdout_preassignment_and_withdrawn_inputs(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    with pytest.raises(ValueError):
        replace(
            assignment,
            assigned_at=assignment.candidate.report.plan.holdout_scope.last_observed_at
            - timedelta(seconds=1),
        )
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    with pytest.raises(ValueError):
        observe_shadow(
            assignment.shadow_id,
            "holdout",
            assignment.candidate.report.adversarial.source_bars[:3],
            store,
            candidates,
            strategies,
        )
    candidates.withdraw(
        assignment.candidate.manifest.candidate_id,
        expected_revision=1,
        command_id="retire",
        reason="Fixture retirement",
    )
    with pytest.raises(ValueError, match="candidate"):
        observe_shadow(assignment.shadow_id, "withdrawn", bars, store, candidates, strategies)
    with pytest.raises(ValueError):
        assign_shadow(replace(assignment, shadow_id="new-shadow"), store, candidates, strategies)


def test_interrupted_and_failed_shadow_batches_block_future_execution(tmp_path, monkeypatch):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_portfolio import ShadowRequest

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    store.reserve(ShadowRequest(assignment.shadow_id, "interrupted", bars[:3]))
    with pytest.raises(ValueError, match="Uncertain"):
        observe_shadow(assignment.shadow_id, "interrupted", bars[:3], store, candidates, strategies)
    with pytest.raises(ValueError, match="Uncertain"):
        observe_shadow(assignment.shadow_id, "next-batch", bars[3:], store, candidates, strategies)


def test_shadow_failed_native_run_is_archived(tmp_path, monkeypatch):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)

    def fail(*args, **kwargs):
        raise ValueError("Private native error")

    monkeypatch.setattr("ai_quant_os.shadow_pipeline.run_registered_backtest", fail)
    bars = forward_bars(assignment)
    report = observe_shadow(
        assignment.shadow_id, "failed-batch", bars[:3], store, candidates, strategies
    )
    assert report.outcome == "FAILED"
    assert report.reason == "NATIVE_EXECUTION_FAILURE"
    assert report.run is None
    assert store.latest(assignment.shadow_id) == report
    with pytest.raises(ValueError, match="failed"):
        observe_shadow(assignment.shadow_id, "next-batch", bars[3:], store, candidates, strategies)


def test_shadow_archive_corruption_and_result_tampering_are_refused(tmp_path):
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    report = observe_shadow(
        assignment.shadow_id, "batch-one", forward_bars(assignment), store, candidates, strategies
    )
    with pytest.raises(ValueError):
        replace(report, promotion_allowed=True)
    with pytest.raises(ValueError):
        replace(
            report, run=replace(report.run, result=replace(report.run.result, net_pnl=Decimal(999)))
        )
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE shadow_batches SET report=?", (b"{}",))
    with pytest.raises(ValueError, match="fingerprint"):
        store.latest(assignment.shadow_id)


def test_future_observations_never_reserve(tmp_path):
    from datetime import datetime, timezone
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    bars = tuple(
        replace(bar, observed_at=future + timedelta(minutes=i))
        for i, bar in enumerate(forward_bars(assignment))
    )
    with pytest.raises(ValueError, match="Future"):
        observe_shadow(assignment.shadow_id, "future", bars, store, candidates, strategies)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM shadow_batches").fetchone()[0] == 0


def test_withdrawal_during_native_execution_archives_failure(tmp_path, monkeypatch):
    from ai_quant_os import shadow_pipeline

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    shadow_pipeline.assign_shadow(assignment, store, candidates, strategies)
    original = shadow_pipeline.run_registered_backtest

    def withdraw(*args, **kwargs):
        run = original(*args, **kwargs)
        candidates.withdraw(
            assignment.candidate.manifest.candidate_id,
            expected_revision=1,
            command_id="withdraw-during-replay",
            reason="Fixture retirement",
        )
        return run

    monkeypatch.setattr(shadow_pipeline, "run_registered_backtest", withdraw)
    report = shadow_pipeline.observe_shadow(
        assignment.shadow_id, "batch", forward_bars(assignment), store, candidates, strategies
    )
    assert report.reason == "CANDIDATE_WITHDRAWN"
    assert report.outcome == "FAILED"
    assert report.run is not None
    assert store.latest(assignment.shadow_id) == report
    from ai_quant_os.strategy_selector_sources import shadow_performance_observation

    performance = shadow_performance_observation(store, now=report.observed_at)
    assert performance.availability == "NO_OBSERVATIONS"
    assert performance.rows[0].outcome == "FAILED"
    assert performance.rows[0].net_pnl == report.run.result.net_pnl
    assert performance.rows[0].failure_reason == "CANDIDATE_WITHDRAWN"
    assert (
        shadow_pipeline.observe_shadow(
            assignment.shadow_id, "batch", forward_bars(assignment), store, candidates, strategies
        )
        == report
    )


def test_shadow_changed_historical_prefix_stops_tracking(tmp_path, monkeypatch):
    from ai_quant_os import shadow_pipeline

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    shadow_pipeline.assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    shadow_pipeline.observe_shadow(
        assignment.shadow_id, "one", bars[:3], store, candidates, strategies
    )
    original = shadow_pipeline.run_registered_backtest

    def changed(*args, **kwargs):
        run = original(*args, **kwargs)
        fills = (replace(run.result.fills[0], price=Decimal(999)), *run.result.fills[1:])
        return replace(run, result=replace(run.result, fills=fills))

    monkeypatch.setattr(shadow_pipeline, "run_registered_backtest", changed)
    report = shadow_pipeline.observe_shadow(
        assignment.shadow_id, "two", bars[3:], store, candidates, strategies
    )
    assert report.reason == "PREFIX_REPRODUCIBILITY_FAILURE"
    assert report.run is None


def test_shadow_concurrent_reservations_and_deleted_head(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ai_quant_os.shadow_pipeline import assign_shadow
    from ai_quant_os.shadow_portfolio import ShadowRequest

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    assign_shadow(assignment, store, candidates, strategies)
    request = ShadowRequest(assignment.shadow_id, "batch", forward_bars(assignment))

    def reserve():
        try:
            return store.reserve(request)[0]
        except ValueError as error:
            assert "Uncertain" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(token is not None for token in tokens) == 1
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM shadow_heads")
    with pytest.raises(ValueError, match="head"):
        store.assignment(assignment.shadow_id)


def test_shadow_command_assignment_retry_and_observation_export(tmp_path, monkeypatch, capsys):
    from ai_quant_os.shadow_cli import main
    from ai_quant_os.shadow_pipeline import assign_shadow
    from ai_quant_os.research_store import encode_research

    assignment, store, candidates, strategies = shadow_fixture(tmp_path)
    # CLI assignments use the current clock; no caller-supplied past cutoff is accepted.
    candidate_id = assignment.candidate.manifest.candidate_id
    argv = [
        "ai-quant-shadow",
        "--workspace",
        str(tmp_path),
        "assign",
        "current-shadow",
        "--candidate-id",
        candidate_id,
    ]
    monkeypatch.setattr("sys.argv", argv)
    main()
    first = store.assignment("current-shadow")
    main()
    assert store.assignment("current-shadow") == first
    assign_shadow(assignment, store, candidates, strategies)
    bars = forward_bars(assignment)
    source = tmp_path / "forward.csv"
    source.write_text(
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(
            f"{bar.instrument_id},{bar.observed_at.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}"
            for bar in bars
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-shadow",
            "--workspace",
            str(tmp_path),
            "observe",
            assignment.shadow_id,
            "--command-id",
            "cli-batch",
            "--bars",
            str(source),
        ],
    )
    main()
    output = tmp_path / "export.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-shadow",
            "--workspace",
            str(tmp_path),
            "latest",
            assignment.shadow_id,
            "--output",
            str(output),
        ],
    )
    main()
    assert output.read_bytes() == encode_research(store.latest(assignment.shadow_id))
    main()
    assert "promotion_allowed=false" in capsys.readouterr().out
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-shadow",
            "--workspace",
            str(tmp_path),
            "latest",
            assignment.shadow_id,
            "--output",
            str(store.path),
        ],
    )
    with pytest.raises(SystemExit):
        main()


def test_saved_shadow_portfolio_replays():
    from pydantic import TypeAdapter
    from ai_quant_os.shadow_portfolio import ShadowReport
    from ai_quant_os.research_store import encode_research

    raw = (
        Path(__file__).parents[1] / "docs/evaluations/shadow-portfolio-baseline.json"
    ).read_bytes()
    report = TypeAdapter(ShadowReport).validate_json(raw)
    assert report.outcome == "OBSERVED"
    assert report.sequence == 2
    assert report.promotion_allowed is False
    assert encode_research(report) == raw


def test_cancelled_selector_scan_keeps_its_bounded_capacity_until_worker_finishes(monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading

    import ai_quant_os.strategy_selector_sources as selector_sources_module

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = 0

    def scan(store, *, now):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            release.wait(2)
            finished.set()
        return selector_sources_module.ShadowPerformanceObservation(
            as_of=now, availability="NO_OBSERVATIONS", rows=()
        )

    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(selector_sources_module, "_SHADOW_SCAN_EXECUTOR", executor)
    monkeypatch.setattr(
        selector_sources_module, "_SHADOW_SCAN_SLOTS", threading.BoundedSemaphore(1)
    )
    monkeypatch.setattr(selector_sources_module, "shadow_performance_observation", scan)

    now = datetime.now().astimezone()
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(selector_sources_module.run_shadow_performance_scan(None, now=now))
        loop.run_until_complete(asyncio.sleep(0))
        assert started.wait(1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task)
        with pytest.raises(selector_sources_module.ShadowScanCapacityExceeded):
            asyncio.run(selector_sources_module.run_shadow_performance_scan(None, now=now))
        loop.close()
        release.set()
        assert finished.wait(1)
        result = asyncio.run(selector_sources_module.run_shadow_performance_scan(None, now=now))
        assert result.availability == "NO_OBSERVATIONS"
    finally:
        if not loop.is_closed():
            loop.close()
        release.set()
        executor.shutdown(wait=True)
