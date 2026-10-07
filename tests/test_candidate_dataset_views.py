"""Development windows retain their training identity and explicit selection overlap."""

import base64
import importlib
import json

import pytest

from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_test_jobs import candidate_test_request
from ai_quant_os.research_data_parsing import parse_research_csv
from test_strategy_prefix_worker import package as package


def views():
    assert importlib.util.find_spec("ai_quant_os.candidate_dataset_views"), (
        "Explicit source-bound dataset views required"
    )
    return importlib.import_module("ai_quant_os.candidate_dataset_views")


def unattested_test_metadata(package):
    request = candidate_test_request(package)
    return CandidateTestResult(
        schema_version="1.0",
        request_ref=request.request_ref,
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        python_version="3.11.17",
        pytest_version="8.4.2",
        compiled_files=6,
        collected=6,
        passed=6,
        failed=0,
        skipped=0,
        status="PROPOSED_TESTS_PASSED_UNATTESTED",
        isolation_verified=False,
        execution_authorized=False,
        promotion_allowed=False,
    )


def test_expanding_windows_preserve_original_training_source_and_exact_closed_prefixes(package):
    module = views()
    training = parse_research_csv(base64.b64decode(package.training.raw_base64))
    for fold, start in ((0, 8), (1, 10)):
        view = module.development_window(package, fold, unattested_test_metadata(package))
        checked, receipt, dataset = module.candidate_dataset_inputs(view)
        assert checked.package_ref == package.package_ref
        assert receipt.resource_class == "TRAINING" and receipt == package.training.receipt
        assert dataset.bars == training.bars[start - 2 : start + 2]
        assert view.window.selection_data_overlap is True and view.window.promotion_allowed is False
        assert view.window.validation_start == start and view.window.validation_stop == start + 2
        from ai_quant_os.candidate_invocations import prepare_invocation

        request = prepare_invocation(view, 2)
        assert tuple(row.to_bar() for row in request.history) == training.bars[start - 2 : start]
        assert request.execute_at == training.bars[start].observed_at
        assert (
            request.source == package.candidate.files[0] and request.sdk_files == package.sdk_files
        )
        assert package.holdout.raw_base64 not in request.model_dump_json()
        request.require_package(view)


@pytest.mark.parametrize("fold", [-1, 2, True])
def test_missing_and_invalid_fold_selection_is_refused(package, fold):
    with pytest.raises(ValueError):
        views().development_window(package, fold, unattested_test_metadata(package))


def test_foreign_or_failed_test_result_cannot_admit_development_view(package):
    module = views()
    for field, value in (
        ("package_ref", "validation-package:" + "0" * 64),
        ("status", "PROPOSED_TESTS_FAILED_UNATTESTED"),
    ):
        report = json.loads(unattested_test_metadata(package).model_dump_json())
        report[field] = value
        with pytest.raises(ValueError):
            module.development_window(package, 0, CandidateTestResult.model_validate(report))


def test_original_holdout_input_behavior_is_preserved(package):
    _, receipt, dataset = views().candidate_dataset_inputs(package)
    assert receipt == package.holdout.receipt
    expected = parse_research_csv(base64.b64decode(package.holdout.raw_base64))
    assert dataset.bars == expected.bars and dataset.fingerprint == expected.fingerprint


def test_development_numeric_tape_and_native_replay_keep_window_identity(package):
    from ai_quant_os.candidate_invocations import prepare_invocation, assemble_unattested_tape
    from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
    from ai_quant_os.signal_tape_replay import replay_signal_tape

    view = views().development_window(package, 0, unattested_test_metadata(package))
    outputs = tuple(
        CandidateTargetOutput(
            invocation_ref=prepare_invocation(view, index).invocation_ref, target=1
        )
        for index in (2, 3)
    )
    tape = assemble_unattested_tape(view, outputs)
    assert tape.dataset_ref == package.training.receipt.dataset_ref
    assert tape.dataset_fingerprint == view.window.window_fingerprint
    assert len(tape.require_package(view).bars) == 4
    replay = replay_signal_tape(view, tape)
    assert replay.native_order_count == 1 and replay.promotion_authorized is False


def test_partial_final_fold_is_explicit_and_retains_all_remaining_bars(package):
    package = type(package).model_validate_json(
        package.model_copy(
            update={"plan": package.plan.model_copy(update={"walk_forward_test_bars": 3})}
        ).model_dump_json()
    )
    module = views()
    first = module.development_window(package, 0, unattested_test_metadata(package))
    final = module.development_window(package, 1, unattested_test_metadata(package))
    assert first.window.complete_declared_window is True
    assert final.window.complete_declared_window is False
    assert (final.window.validation_start, final.window.validation_stop) == (11, 12)
    _, _, selected = module.candidate_dataset_inputs(final)
    assert len(selected.bars) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"history_start": 5},
        {"validation_stop": 9, "validation_start": 10},
    ],
)
def test_window_wire_record_refuses_invalid_closed_history_or_empty_validation(package, changes):
    module = views()
    window = module.development_window(package, 0, unattested_test_metadata(package)).window
    payload = window.model_dump() | changes
    with pytest.raises(ValueError):
        module.CandidateDatasetWindow.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"fold_index": 1},
        {"window_fingerprint": "0" * 64},
        {"original_dataset_fingerprint": "0" * 64},
        {"test_result_sha256": "0" * 64},
        {"complete_declared_window": False},
    ],
)
def test_reconstructed_dataset_input_refuses_substituted_window_bindings(package, changes):
    module = views()
    view = module.development_window(package, 0, unattested_test_metadata(package))
    forged = module.CandidateDatasetView(
        view.input_package, view.window.model_copy(update=changes), view.test_result
    )
    with pytest.raises(ValueError):
        module.candidate_dataset_inputs(forged)
