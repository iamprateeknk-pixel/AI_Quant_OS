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


def assign_shadow(assignment, store, candidates, strategies, *, reality_gap_policy=None):
    existing = store.lookup(assignment.shadow_id)
    if existing is not None:
        if existing != assignment:
            raise ValueError("Immutable shadow candidate assignment cannot change")
        return store.assign(assignment, reality_gap_policy=reality_gap_policy)
    if candidates.snapshot(assignment.candidate.manifest.candidate_id) != (
        assignment.candidate_view,
        assignment.candidate,
    ):
        raise ValueError("Frozen shadow candidate bundle mismatch")
    _prepare(assignment, strategies)
    return store.assign(assignment, reality_gap_policy=reality_gap_policy)


def _apply_reality_gap(report, candidates, policy):
    if policy is None or report.outcome != "OBSERVED":
        return None
    from ai_quant_os.shadow_reality_gap import assess_shadow_reality_gap

    assessment = assess_shadow_reality_gap(report, policy)
    if assessment.state != "DEMOTION_REQUIRED":
        return assessment
    view = candidates.get(assessment.candidate_id)
    if view is None:
        raise ValueError("Reality-gap candidate is missing from its registry")
    return candidates.demote_for_reality_gap(report, assessment, expected_revision=view.revision)


def observe_shadow(
    shadow_id,
    command_id,
    bars,
    store,
    candidates,
    strategies,
    *,
    recovery_id=None,
    recovery_reason=None,
):
    request = ShadowRequest(shadow_id, command_id, bars)
    if (recovery_id is None) != (recovery_reason is None):
        raise ValueError("Recovery ID and reason must be supplied together")
    reality_gap_policy = store.reality_gap_policy(shadow_id)
    cached = store.cached(request)
    if cached is not None:
        _apply_reality_gap(cached, candidates, reality_gap_policy)
        return cached
    observed_at = utc_now()
    if bars[-1].observed_at > observed_at:
        raise ValueError("Future shadow observations are not accepted")
    _eligible(store.assignment(shadow_id), candidates)
    if recovery_id is None:
        token, assignment, source, previous, cached = store.reserve(request)
    else:
        token, assignment, source, previous, cached = store.recover_reservation(
            request,
            recovery_id,
            recovery_reason,
            recovered_at=observed_at,
        )
    if cached is not None:
        _apply_reality_gap(cached, candidates, reality_gap_policy)
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
    _apply_reality_gap(report, candidates, reality_gap_policy)
    return report
