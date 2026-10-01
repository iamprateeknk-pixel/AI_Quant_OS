"""Forward diagnostic portfolio tracking through isolated native engines."""

from datetime import datetime, timezone

from ai_quant_os.market_data import BarDataset
from ai_quant_os.registered_backtest import run_registered_backtest
from ai_quant_os.shadow_portfolio import ShadowReport, ShadowRequest
from ai_quant_os.strategy_registry import prepare_builtin


def utc_now():
    return datetime.now(timezone.utc)


def _eligible(assignment, candidates):
    if candidates.get(assignment.candidate.manifest.candidate_id) != assignment.candidate_view:
        raise ValueError("Shadow candidate lifecycle changed")


def _prepare(assignment, strategies):
    parent = assignment.candidate.report.holdout
    _, binding = prepare_builtin(parent.binding.genome, strategies)
    if binding != parent.binding:
        raise ValueError("Frozen shadow implementation changed")
    return parent


def assign_shadow(assignment, store, candidates, strategies):
    if candidates.snapshot(assignment.candidate.manifest.candidate_id) != (
        assignment.candidate_view,
        assignment.candidate,
    ):
        raise ValueError("Frozen shadow candidate bundle mismatch")
    _prepare(assignment, strategies)
    return store.assign(assignment)


def observe_shadow(shadow_id, command_id, bars, store, candidates, strategies):
    request = ShadowRequest(shadow_id, command_id, bars)
    cached = store.cached(request)
    if cached is not None:
        return cached
    observed_at = utc_now()
    if bars[-1].observed_at > observed_at:
        raise ValueError("Future shadow observations are not accepted")
    _eligible(store.assignment(shadow_id), candidates)
    token, assignment, source, previous, cached = store.reserve(request)
    if cached is not None:
        return cached
    run, reason = None, None
    stage = "SOURCE_VERIFICATION_FAILURE"
    try:
        parent = _prepare(assignment, strategies)
        plan = assignment.candidate.report.plan
        stage = "NATIVE_EXECUTION_FAILURE"
        run = run_registered_backtest(
            BarDataset(source),
            parent.binding.genome,
            plan.costs,
            strategies,
            quantity=plan.quantity,
        )
        stage = "CANDIDATE_WITHDRAWN"
        _eligible(assignment, candidates)
        if previous is not None:
            stage = "PREFIX_REPRODUCIBILITY_FAILURE"
            count = len(previous.run.result.fills)
            if run.result.fills[:count] != previous.run.result.fills or any(
                fill.observed_at <= previous.source_bars[-1].observed_at
                for fill in run.result.fills[count:]
            ):
                raise ValueError("Shadow fill prefix changed")
    except (ValueError, RuntimeError, ArithmeticError, OSError):
        reason = stage
        if stage != "CANDIDATE_WITHDRAWN":
            run = None
    report = ShadowReport(
        assignment,
        request,
        1 if previous is None else previous.sequence + 1,
        "0" * 64 if previous is None else previous.fingerprint,
        source,
        observed_at,
        "OBSERVED" if reason is None else "FAILED",
        run,
        reason,
    )
    store.finish(token, report)
    return report
