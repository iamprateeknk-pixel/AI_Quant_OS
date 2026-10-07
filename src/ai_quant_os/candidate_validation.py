"""Bind complete original-source run inventories to descriptive validation screens."""

from decimal import Context, Decimal, localcontext
from hashlib import sha256

from ai_quant_os.candidate_dataset_run_contracts import CandidateDatasetRunReport
from ai_quant_os.candidate_dataset_views import (
    candidate_dataset_inputs,
    development_window,
    _checked_tests,
)
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_validation_contracts import (
    CandidateValidationPolicy,
    CandidateValidationScorecard,
    CandidateValidationGate,
    PENDING_GATES,
)
from ai_quant_os.candidate_validation_monte_carlo import evaluate_candidate_monte_carlo
from ai_quant_os.monte_carlo import CashFill
from ai_quant_os.strategy_execution_tape import verify_strategy_execution_replay
from ai_quant_os.strategy_prefix_run_contracts import StrategyPrefixRunReport
from ai_quant_os.validation_package_contracts import ValidationPackage


def _checked_reports(package, tested, holdout, windows):
    if type(package) is not ValidationPackage or type(holdout) is not StrategyPrefixRunReport:
        raise ValueError("Original package and original holdout report required")
    package, _, _ = candidate_dataset_inputs(package)
    tested = _checked_tests(package, tested)
    holdout = StrategyPrefixRunReport.model_validate_json(holdout.model_dump_json())
    count = (
        package.training.receipt.row_count
        - package.plan.walk_forward_train_bars
        + package.plan.walk_forward_test_bars
        - 1
    ) // package.plan.walk_forward_test_bars
    if type(windows) is not tuple or len(windows) != count:
        raise ValueError("Every declared development interval, including partial tails, required")
    checked = []
    for index, window in enumerate(windows):
        if type(window) is not CandidateDatasetRunReport:
            raise ValueError("Exact development report required")
        window = CandidateDatasetRunReport.model_validate_json(window.model_dump_json())
        view = development_window(package, index, tested)
        if window.manifest.dataset_window != view.window:
            raise ValueError("Development reports differ from original ordered intervals")
        checked.append((view, window))
    reports = ((package, holdout), *checked)
    identities = []
    expected_image = holdout.manifest.image_inputs.model_dump(exclude={"archive", "base_metadata"})
    for context, report in reports:
        if (
            report.manifest.package_ref,
            report.manifest.candidate_ref,
            report.manifest.image_inputs.model_dump(exclude={"archive", "base_metadata"}),
            report.manifest.quantity,
            report.manifest.initial_balance,
        ) != (
            package.package_ref,
            package.candidate.candidate_ref,
            expected_image,
            holdout.manifest.quantity,
            holdout.manifest.initial_balance,
        ):
            raise ValueError("Validation runs differ in original source/image/quantity/balance")
        for replay in (report.replay, report.stress_replay):
            verify_strategy_execution_replay(context, replay)
        identities.extend(row.exit.container_id for row in report.replay.collection.receipts)
    if len(set(identities)) != len(identities):
        raise ValueError("Validation reused a worker completion across intervals")
    return package, tested, holdout, tuple(report for _, report in checked)


def _gate(name, passed, reason, refs):
    return CandidateValidationGate(
        name=name, state="PASS" if passed else "FAIL", reason=reason, evidence_refs=refs
    )


def evaluate_candidate(
    package: ValidationPackage,
    tested: CandidateTestResult,
    holdout: StrategyPrefixRunReport,
    windows: tuple[CandidateDatasetRunReport, ...],
    *,
    policy: CandidateValidationPolicy | None = None,
) -> CandidateValidationScorecard:
    package, tested, holdout, windows = _checked_reports(package, tested, holdout, windows)
    policy = CandidateValidationPolicy.model_validate_json(
        (policy if policy is not None else CandidateValidationPolicy()).model_dump_json()
    )
    with localcontext(Context(prec=28)):
        native = holdout.replay.replay
        test_hash = sha256(tested.model_dump_json().encode()).hexdigest()
        holdout_refs = (holdout.report_ref,)
        window_refs = tuple(row.report_ref for row in windows)
        all_refs = holdout_refs + window_refs
        development_pnl = sum((row.replay.replay.net_pnl for row in windows), Decimal(0))
        fraction = Decimal(sum(row.replay.replay.net_pnl >= 0 for row in windows)) / len(windows)
        monte_carlo = evaluate_candidate_monte_carlo(
            tuple(CashFill(row.side, row.quantity, row.price, row.fee) for row in native.fills),
            final_position=native.final_position,
            net_pnl=native.net_pnl,
            pnl_currency=native.pnl_currency,
            plan=package.plan,
            policy=policy,
        )
        complete = all(row.manifest.dataset_window.complete_declared_window for row in windows)
        enough = len(windows) >= policy.minimum_validation_windows
        interval_reason = (
            "PARTIAL_DECLARED_WINDOW" if not complete else "INSUFFICIENT_VALIDATION_WINDOWS"
        )
        gates = [
            _gate(
                "PROPOSED_TESTS", True, "UNATTESTED_PROPOSED_CASES_PASSED", ("sha256:" + test_hash,)
            ),
            _gate(
                "OUT_OF_SAMPLE", native.net_pnl >= 0, "DECLARED_HOLDOUT_PNL_SCREEN", holdout_refs
            ),
            _gate(
                "WALK_FORWARD",
                development_pnl >= 0,
                "RETROSPECTIVE_DEVELOPMENT_PNL_SCREEN",
                window_refs,
            ),
            _gate(
                "STABILITY",
                fraction >= policy.minimum_nonnegative_window_fraction,
                "DECLARED_NONNEGATIVE_WINDOW_FRACTION_SCREEN",
                window_refs,
            ),
            _gate(
                "COST_STRESS",
                all(row.stress_replay.replay.net_pnl >= 0 for row in (holdout, *windows)),
                "DECLARED_SYNTHETIC_STRESS_PNL_SCREEN",
                all_refs,
            ),
            _gate(
                "MINIMUM_ACTIVITY",
                all(
                    row.replay.replay.native_order_count >= policy.minimum_orders_per_window
                    for row in (holdout, *windows)
                ),
                "DECLARED_NATIVE_ORDER_ACTIVITY_SCREEN",
                all_refs,
            ),
            CandidateValidationGate(
                name="MONTE_CARLO",
                state=monte_carlo.state,
                reason=monte_carlo.reason,
                evidence_refs=holdout_refs,
            ),
        ]
        if not complete or not enough:
            for index in (2, 3):
                gates[index] = CandidateValidationGate(
                    name=gates[index].name,
                    state="PENDING",
                    reason=interval_reason,
                    evidence_refs=window_refs,
                )
        reasons = (
            "PHYSICAL_AND_STATISTICAL_ISOLATION_UNASSESSED",
            "INDEPENDENT_SOURCE_DAEMON_ATTESTATION_MISSING",
            "PROPOSED_SOURCE_ADVERSARIAL_VALIDATION_MISSING",
            "BROKER_MARKET_LIQUIDITY_REALITY_GAP_UNASSESSED",
            "FINANCING_COSTS_UNAVAILABLE",
            "ACCOUNT_CURRENCY_CONVERSION_UNAVAILABLE",
            "INDEPENDENT_CRITICAL_REVIEW_MISSING",
            "SHADOW_OBSERVATIONS_MISSING",
        )
        gates.extend(
            CandidateValidationGate(
                name=name, state="PENDING", reason=reason, evidence_refs=all_refs
            )
            for name, reason in zip(PENDING_GATES, reasons, strict=True)
        )
        failed = tuple(row.name + ":" + row.reason for row in gates if row.state == "FAIL")
        pending = tuple(row.name + ":" + row.reason for row in gates if row.state == "PENDING")
        return CandidateValidationScorecard(
            package_ref=package.package_ref,
            candidate_ref=package.candidate.candidate_ref,
            test_result_sha256=test_hash,
            holdout_report_ref=holdout.report_ref,
            window_report_refs=window_refs,
            policy=policy,
            holdout_net_pnl=native.net_pnl,
            development_net_pnl=development_pnl,
            nonnegative_window_fraction=fraction,
            pnl_currency=native.pnl_currency,
            monte_carlo=monte_carlo,
            gates=tuple(gates),
            outcome="REJECTED" if failed else "VALIDATION_INCOMPLETE",
            reasons=failed or pending,
        )


def verify_candidate_validation(
    package: ValidationPackage,
    tested: CandidateTestResult,
    holdout: StrategyPrefixRunReport,
    windows: tuple[CandidateDatasetRunReport, ...],
    scorecard: CandidateValidationScorecard,
) -> None:
    if type(scorecard) is not CandidateValidationScorecard:
        raise ValueError("Exact candidate scorecard required")
    checked = CandidateValidationScorecard.model_validate_json(scorecard.model_dump_json())
    if checked != evaluate_candidate(package, tested, holdout, windows, policy=checked.policy):
        raise ValueError("Candidate scorecard differs from original numeric evidence and policy")
