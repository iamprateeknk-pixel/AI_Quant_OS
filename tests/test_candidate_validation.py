"""Source-bound numeric scorecards retain every missing acceptance gate."""

import importlib
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon
from test_candidate_dataset_views import unattested_test_metadata


def validation():
    assert importlib.util.find_spec("ai_quant_os.candidate_validation"), (
        "Original-source candidate statistical validation required"
    )
    return importlib.import_module("ai_quant_os.candidate_validation")


def observed_reports(package, scripted):
    from ai_quant_os.candidate_dataset_views import development_window
    from ai_quant_os.strategy_prefix_run import prepare_strategy_prefix_run, run_strategy_prefixes

    root, args, _, _ = scripted
    tested = unattested_test_metadata(package)
    manifest = prepare_strategy_prefix_run(package, root=root, **args)
    holdout = run_strategy_prefixes(package, root=root, run_sha256=manifest.run_sha256)
    windows = []
    for fold in (0, 1):
        view = development_window(package, fold, tested)
        fold_root = root.parent / ("fold-" + str(fold))
        fold_root.mkdir()
        manifest = prepare_strategy_prefix_run(view, root=fold_root, **args)
        windows.append(run_strategy_prefixes(view, root=fold_root, run_sha256=manifest.run_sha256))
    return tested, holdout, tuple(windows)


def test_complete_scorecard_binds_native_reports_and_never_skips_missing_gates(
    package, scripted, tmp_path
):
    module = validation()
    tested, holdout, windows = observed_reports(package, scripted)
    score = module.evaluate_candidate(package, tested, holdout, windows)
    assert score.package_ref == package.package_ref
    assert score.candidate_ref == package.candidate.candidate_ref
    assert score.holdout_report_ref == holdout.report_ref
    assert score.window_report_refs == tuple(row.report_ref for row in windows)
    assert score.holdout_net_pnl == holdout.replay.replay.net_pnl
    assert score.development_net_pnl == sum(
        (row.replay.replay.net_pnl for row in windows), Decimal(0)
    )
    assert tuple(row.name for row in score.gates) == (
        "PROPOSED_TESTS",
        "OUT_OF_SAMPLE",
        "WALK_FORWARD",
        "STABILITY",
        "COST_STRESS",
        "MINIMUM_ACTIVITY",
        "MONTE_CARLO",
        "INDEPENDENT_HOLDOUT",
        "SOURCE_ATTESTATION",
        "ADVERSARIAL",
        "REALITY_GAP",
        "FINANCING",
        "ACCOUNT_CONVERSION",
        "INDEPENDENT_REVIEW",
        "SHADOW",
    )
    assert score.promotion_allowed is False and score.isolation_verified is False
    gates = {row.name: row for row in score.gates}
    assert gates["INDEPENDENT_HOLDOUT"].state == "PENDING"
    assert gates["SOURCE_ATTESTATION"].state == "PENDING"
    assert gates["MONTE_CARLO"].reason == "INSUFFICIENT_CLOSED_TRADES"
    assert score.monte_carlo.closed_trade_count == 2
    assert score.monte_carlo.requested_trials == package.plan.monte_carlo_trials
    assert score.outcome in ("REJECTED", "VALIDATION_INCOMPLETE")
    module.verify_candidate_validation(package, tested, holdout, windows, score)
    portable = windows[0].model_copy(
        update={
            "manifest": windows[0].manifest.model_copy(
                update={
                    "image_inputs": windows[0].manifest.image_inputs.model_copy(
                        update={
                            "archive": "D:/another-evidence-host/worker/image.oci.tar",
                            "base_metadata": "D:/another-evidence-host/worker/base",
                        }
                    )
                }
            )
        }
    )
    relocated = module.evaluate_candidate(package, tested, holdout, (portable, windows[1]))
    assert relocated.holdout_net_pnl == score.holdout_net_pnl
    assert relocated.development_net_pnl == score.development_net_pnl
    assert relocated.promotion_allowed is False
    for invalid in (windows[:1], windows[::-1], windows[:1] * 2):
        with pytest.raises(ValueError):
            module.evaluate_candidate(package, tested, holdout, invalid)
    forged = score.model_copy(update={"holdout_net_pnl": score.holdout_net_pnl + 1})
    with pytest.raises(ValueError):
        module.verify_candidate_validation(package, tested, holdout, windows, forged)
    records = [("test-result.json", tested), ("holdout.json", holdout)] + [
        ("window-" + str(index) + ".json", report) for index, report in enumerate(windows)
    ]
    common = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_validation_cli",
        "--validation-archive",
        str(tmp_path / "service.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
    ]
    for index, (name, record) in enumerate(records):
        path = tmp_path / name
        raw = record.model_dump_json().encode()
        path.write_bytes(raw)
        common.extend(
            [
                "--test-result"
                if index == 0
                else "--holdout-report"
                if index == 1
                else "--development-report",
                str(path),
                sha256(raw).hexdigest(),
            ]
        )
    output = tmp_path / "scorecard.json"
    child = subprocess.run(
        common + ["evaluate", "--output", str(output)], capture_output=True, timeout=30
    )
    assert child.returncode == 0, child.stderr.decode()
    assert json.loads(child.stdout)["scorecard_ref"] == score.scorecard_ref
    assert output.read_bytes() == score.model_dump_json().encode()
    pin = sha256(output.read_bytes()).hexdigest()
    child = subprocess.run(
        common + ["verify", "--scorecard", str(output), pin], capture_output=True, timeout=30
    )
    assert child.returncode == 0, child.stderr.decode()
    assert json.loads(child.stdout)["verified_binding"] is True
    child = subprocess.run(
        common + ["evaluate", "--output", str(output)], capture_output=True, timeout=30
    )
    assert child.returncode != 0 and output.read_bytes() == score.model_dump_json().encode()
    archive_path = tmp_path / "checked-candidate-results.sqlite3"
    child = subprocess.run(
        common + ["--record-archive", str(archive_path), "archive-evaluation"],
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr.decode()
    from ai_quant_os.candidate_validation_store import CandidateValidationStore

    with CandidateValidationStore(archive_path) as archive:
        assert archive.get(json.loads(child.stdout)["scorecard_ref"]) == score


def test_closed_trade_monte_carlo_honors_declared_seed_trials_and_currency(package):
    module = validation()
    from ai_quant_os.monte_carlo import CashFill

    fills = tuple(
        fill
        for _ in range(20)
        for fill in (
            CashFill("BUY", Decimal(1), Decimal(100), Decimal(0)),
            CashFill("SELL", Decimal(1), Decimal(102), Decimal(0)),
        )
    )
    result = module.evaluate_candidate_monte_carlo(
        fills,
        final_position=Decimal(0),
        net_pnl=Decimal(40),
        pnl_currency="USD",
        plan=package.plan,
        policy=module.CandidateValidationPolicy(),
    )
    assert result.state == "PASS" and result.closed_trade_count == 20
    assert result.requested_trials == package.plan.monte_carlo_trials
    assert result.result.spec.seed == package.plan.random_seed
    assert result.result.lower_tail_pnl == Decimal(40)
    assert result.result.simulation_sha256
    with pytest.raises(ValueError):
        module.evaluate_candidate_monte_carlo(
            fills,
            final_position=Decimal(1),
            net_pnl=Decimal(40),
            pnl_currency="USD",
            plan=package.plan,
            policy=module.CandidateValidationPolicy(),
        )


def test_unadmitted_trial_capacity_is_retained_without_lowering_the_plan(package):
    module = validation()
    from ai_quant_os.monte_carlo import CashFill

    plan = type(package.plan).model_validate_json(
        package.plan.model_copy(update={"monte_carlo_trials": 100000}).model_dump_json()
    )
    evidence = module.evaluate_candidate_monte_carlo(
        (
            CashFill("BUY", Decimal(1), Decimal(100), Decimal(0)),
            CashFill("SELL", Decimal(1), Decimal(102), Decimal(0)),
        ),
        final_position=Decimal(0),
        net_pnl=Decimal(2),
        pnl_currency="USD",
        plan=plan,
        policy=module.CandidateValidationPolicy(),
    )
    assert evidence.requested_trials == 100000 and evidence.result is None
    assert evidence.state == "PENDING"
    assert evidence.reason == "DECLARED_COMPUTATION_CAPACITY_EXCEEDED"


def test_open_native_position_never_becomes_an_invented_closed_trade(package):
    module = validation()
    from ai_quant_os.monte_carlo import CashFill

    evidence = module.evaluate_candidate_monte_carlo(
        (CashFill("BUY", Decimal(1), Decimal(100), Decimal(0)),),
        final_position=Decimal(1),
        net_pnl=Decimal(2),
        pnl_currency="USD",
        plan=package.plan,
        policy=module.CandidateValidationPolicy(),
    )
    assert evidence.closed_trade_count == 0 and evidence.open_position == 1
    assert evidence.state == "PENDING" and evidence.reason == "UNCLOSED_POSITION"


def test_gate_evidence_refuses_unbound_free_text():
    from ai_quant_os.candidate_validation_contracts import CandidateValidationGate

    with pytest.raises(ValueError):
        CandidateValidationGate(
            name="OUT_OF_SAMPLE",
            state="PASS",
            reason="DECLARED_HOLDOUT_PNL_SCREEN",
            evidence_refs=("trust me",),
        )


@pytest.mark.parametrize(
    "name,model_name",
    [
        ("candidate-validation-policy", "CandidateValidationPolicy"),
        ("candidate-validation-scorecard", "CandidateValidationScorecard"),
    ],
)
def test_candidate_validation_wire_contract_is_retained(name, model_name):
    from ai_quant_os import candidate_validation_contracts

    path = Path(__file__).resolve().parents[1] / "docs/schemas" / (name + ".schema.json")
    assert path.exists(), "Candidate validation wire schema required"
    assert (
        json.loads(path.read_bytes())
        == getattr(candidate_validation_contracts, model_name).model_json_schema()
    )
