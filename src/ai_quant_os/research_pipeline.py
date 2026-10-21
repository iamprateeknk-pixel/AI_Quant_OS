"""Development-only selection with native walk-forward, held-out and cost-stress evidence."""

from decimal import Context, localcontext

from ai_quant_os.market_data import BarDataset
from ai_quant_os.registered_backtest import run_registered_backtest
from ai_quant_os.research_contracts import ResearchDatasetScope, ResearchPlan, partition_development
from ai_quant_os.research_results import (
    FoldEvidence,
    PENDING_GATES,
    ResearchGate,
    ResearchReport,
    ResearchSelection,
    select_winner,
    selection_diagnostic,
    measured_conditions,
)
from ai_quant_os.research_store import ResearchStore
from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin
from ai_quant_os.research_monte_carlo import evaluate_native_monte_carlo
from ai_quant_os.adversarial import adversarial_datasets
from ai_quant_os.research_adversarial import (
    NativeAdversarial,
    AdversarialScenario,
    adversarial_score,
)
from ai_quant_os.directional_strategy import DIRECTIONAL_REF
from ai_quant_os.monte_carlo import REVERSAL_MONTE_CARLO_METHOD


def run_research_experiment(
    plan: ResearchPlan,
    development: BarDataset,
    holdout: BarDataset,
    registry: StrategyRegistry,
    store: ResearchStore,
) -> ResearchReport:
    with localcontext(Context(prec=28)):
        return _run(
            plan,
            BarDataset(tuple(development.bars)),
            BarDataset(tuple(holdout.bars)),
            registry,
            store,
        )


def _run(plan, development, holdout, registry, store):
    if (
        plan.monte_carlo_spec is not None
        and any(row.strategy_ref == DIRECTIONAL_REF for row in plan.candidates)
        and plan.monte_carlo_spec.method != REVERSAL_MONTE_CARLO_METHOD
    ):
        raise ValueError("Directional research needs an explicit reversal-fee Monte Carlo policy")
    if (
        development.fingerprint != plan.development_fingerprint
        or holdout.fingerprint != plan.holdout_fingerprint
    ):
        raise ValueError("Research input fingerprint changed")
    if (
        not development.bars
        or not 2 <= len(holdout.bars) <= 4096
        or len({row.instrument_id for row in development.bars + holdout.bars}) != 1
        or development.bars[-1].observed_at >= holdout.bars[0].observed_at
    ):
        raise ValueError(
            "Development and holdout must be chronologically separated for one instrument"
        )
    partitions = partition_development(
        development, train_bars=plan.train_bars, validation_bars=plan.validation_bars
    )
    if plan.schema_version == "1.3" and (len(partitions) < 4 or len(partitions) % 2):
        raise ValueError("Research 1.3 requires an even set of at least four validation folds")
    scenario_count = 2 if plan.adversarial_spec is not None else 0
    if (
        plan.adversarial_spec is not None
        and len(holdout.bars) < 2 * plan.adversarial_spec.drop_every
    ):
        raise ValueError("Insufficient observations for declared adversarial policy")
    validation_runs = len(plan.candidates) if plan.schema_version == "1.3" else 1
    native_calls = (
        len(plan.candidates) * (len(partitions) + 1)
        + (validation_runs + 1) * len(partitions)
        + 2
        + scenario_count
    )
    if native_calls > 128:
        raise ValueError("Native research execution budget exceeded")
    total_rows = (
        len(plan.candidates)
        * (sum(len(train.bars) for train, _ in partitions) + len(development.bars))
        + (validation_runs + 1) * sum(len(validation.bars) for _, validation in partitions)
        + 2 * len(holdout.bars)
        + scenario_count * len(holdout.bars)
    )
    if total_rows > 20000:
        raise ValueError("Research evidence volume budget exceeded")
    if (
        plan.monte_carlo_spec is not None
        and len(holdout.bars)
        * plan.monte_carlo_spec.iterations
        // (1 if any(row.strategy_ref == DIRECTIONAL_REF for row in plan.candidates) else 2)
        > 500000
    ):
        raise ValueError("Declared Monte Carlo computation budget exceeded")
    actual_scope = ResearchDatasetScope(
        holdout.bars[0].instrument_id,
        holdout.bars[0].observed_at,
        holdout.bars[-1].observed_at,
        holdout.fingerprint,
    )
    if actual_scope != plan.holdout_scope:
        raise ValueError("Holdout resource scope changed")
    actual_development = ResearchDatasetScope(
        development.bars[0].instrument_id,
        development.bars[0].observed_at,
        development.bars[-1].observed_at,
        development.fingerprint,
    )
    if actual_development != plan.development_scope:
        raise ValueError("Development resource scope changed")
    reservation = store.reserve(plan)
    if reservation.cached_report is not None:
        return reservation.cached_report
    token = reservation.token
    folds, selection, attempts = [], None, []
    failure_code = "INVALID_CANDIDATE"
    try:
        genomes = tuple(prepare_builtin(genome, registry)[1].genome for genome in plan.candidates)
        if len({row.fingerprint for row in genomes}) != len(genomes):
            raise ValueError("Candidates resolve to duplicate genomes")
        failure_code = "NATIVE_EXECUTION_FAILURE"

        def execute(dataset, genome, costs):
            completed = run_registered_backtest(
                dataset, genome, costs, registry, quantity=plan.quantity
            )
            attempts.append(completed)
            return completed

        def train(dataset):
            return tuple(execute(dataset, genome, plan.costs) for genome in genomes)

        for index, (training, validation) in enumerate(partitions):
            results = train(training)
            winner = select_winner(results).binding.genome
            validation_candidates = train(validation) if plan.schema_version == "1.3" else ()
            validation_result = (
                next(row for row in validation_candidates if row.binding.genome == winner)
                if validation_candidates
                else execute(validation, winner, plan.costs)
            )
            folds.append(
                FoldEvidence(
                    index,
                    results,
                    validation_result,
                    execute(validation, winner, plan.stress_costs),
                    validation_candidates,
                )
            )
        final_training = train(development)
        final_selection = ResearchSelection(
            select_winner(final_training).binding.genome, development.fingerprint, final_training
        )
        failure_code = "HOLDOUT_ACCESS_DENIED"
        store.freeze_selection(plan.experiment_id, token, final_selection)
        selection = final_selection
        failure_code = "NATIVE_EXECUTION_FAILURE"
        heldout = execute(holdout, selection.genome, plan.costs)
        heldout_stress = execute(holdout, selection.genome, plan.stress_costs)
        failure_code = "MONTE_CARLO_FAILURE"
        monte_carlo = (
            evaluate_native_monte_carlo(heldout, plan.monte_carlo_spec)
            if plan.monte_carlo_spec is not None
            else None
        )
        failure_code = "ADVERSARIAL_EXECUTION_FAILURE"
        adversarial = None
        if plan.adversarial_spec is not None:
            scenarios = adversarial_datasets(holdout, plan.adversarial_spec)
            runs = tuple(
                AdversarialScenario(
                    name, dataset.fingerprint, execute(dataset, selection.genome, plan.stress_costs)
                )
                for name, dataset in scenarios
            )
            adversarial = NativeAdversarial(
                tuple(holdout.bars), runs, *adversarial_score(runs, plan.adversarial_spec)
            )
        conditions = measured_conditions(folds, heldout, heldout_stress)
        gates = tuple(
            ResearchGate(
                name,
                "PASS" if passed else "FAIL",
                "DECLARED_SCREEN_PASSED" if passed else "DECLARED_SCREEN_FAILED",
            )
            for name, passed in conditions
        ) + tuple(
            ResearchGate(name, "PENDING", "IMPLEMENTATION_OR_EVIDENCE_MISSING")
            for name in PENDING_GATES
            if name != "MONTE_CARLO" or monte_carlo is None
            if name != "ADVERSARIAL" or adversarial is None
        )
        if monte_carlo is not None:
            gates += (
                ResearchGate("MONTE_CARLO", monte_carlo.result.state, monte_carlo.result.reason),
            )
        if adversarial is not None:
            gates += (ResearchGate("ADVERSARIAL", adversarial.state, adversarial.reason),)
        reasons = tuple(gate.name for gate in gates if gate.state != "PASS")
        outcome = (
            "REJECTED" if any(gate.state == "FAIL" for gate in gates) else "VALIDATION_INCOMPLETE"
        )
        report = ResearchReport(
            plan.schema_version,
            plan,
            outcome,
            False,
            selection,
            tuple(folds),
            heldout,
            heldout_stress,
            gates,
            reasons,
            tuple(attempts),
            monte_carlo,
            adversarial,
            selection_diagnostic(folds) if plan.schema_version == "1.3" else None,
        )
    except Exception:
        report = ResearchReport(
            plan.schema_version,
            plan,
            "FAILED",
            False,
            selection,
            tuple(folds),
            None,
            None,
            (),
            (failure_code,),
            tuple(attempts),
        )
    store.finish(plan.experiment_id, token, report)
    return report
