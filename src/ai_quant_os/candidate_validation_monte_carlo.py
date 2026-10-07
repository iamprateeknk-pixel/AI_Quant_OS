"""Closed native cash-flow resampling with explicit declared-capacity refusal."""

from decimal import Context, Decimal, localcontext

from ai_quant_os.candidate_validation_contracts import (
    CandidateMonteCarloEvidence,
    CandidateValidationPolicy,
)
from ai_quant_os.monte_carlo import (
    CashFill,
    MonteCarloSpec,
    REVERSAL_MONTE_CARLO_METHOD,
    closed_trade_series,
    run_monte_carlo,
)
from ai_quant_os.validation_package_contracts import ValidationPlan


def evaluate_candidate_monte_carlo(
    fills: tuple[CashFill, ...],
    *,
    final_position: Decimal,
    net_pnl: Decimal,
    pnl_currency: str,
    plan: ValidationPlan,
    policy: CandidateValidationPolicy,
) -> CandidateMonteCarloEvidence:
    plan = ValidationPlan.model_validate_json(plan.model_dump_json())
    policy = CandidateValidationPolicy.model_validate_json(policy.model_dump_json())
    if pnl_currency != policy.risk_currency:
        raise ValueError("Monte Carlo policy currency differs from native P&L")
    with localcontext(Context(prec=28)):
        series = closed_trade_series(fills, reversal_fee_policy="EQUAL_SIZE_HALF_FEE_V1")
        if series.open_position != final_position or (
            not series.open_position and sum(series.net_pnls, Decimal(0)) != net_pnl
        ):
            raise ValueError("Closed-trade sample differs from the native numeric ledger")
        capacity = plan.monte_carlo_trials > 2000 or (
            not series.open_position
            and len(series.net_pnls)
            >= max(policy.minimum_closed_trades, policy.monte_carlo_block_size)
            and len(series.net_pnls) * plan.monte_carlo_trials > 500000
        )
        result = (
            None
            if capacity
            else run_monte_carlo(
                series,
                MonteCarloSpec(
                    seed=plan.random_seed,
                    iterations=plan.monte_carlo_trials,
                    block_size=policy.monte_carlo_block_size,
                    min_closed_trades=policy.minimum_closed_trades,
                    lower_tail_percent=policy.lower_tail_percent,
                    minimum_positive_fraction=policy.minimum_positive_fraction,
                    drawdown_limit=policy.drawdown_limit,
                    risk_currency=policy.risk_currency,
                    method=REVERSAL_MONTE_CARLO_METHOD,
                ),
            )
        )
        return CandidateMonteCarloEvidence(
            requested_trials=plan.monte_carlo_trials,
            requested_seed=plan.random_seed,
            closed_trade_count=len(series.net_pnls),
            open_position=series.open_position,
            sample_fingerprint=series.fingerprint,
            state=result.state if result else "PENDING",
            reason=result.reason if result else "DECLARED_COMPUTATION_CAPACITY_EXCEEDED",
            result=result,
        )
