"""Serial original-source validation with audited dispatch and explicit recovery."""

from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from ai_quant_os.candidate_dataset_views import (
    _checked_package,
    _checked_tests,
    development_window,
)
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_test_custody import restore, persist
from ai_quant_os.candidate_test_execution import (
    prepare_candidate_tests,
    read_candidate_test_result,
    run_candidate_tests,
)
from ai_quant_os.candidate_test_jobs import candidate_test_request
from ai_quant_os.candidate_prerequisite_failure import record_prerequisite_failure
from ai_quant_os.candidate_validation_job_contracts import (
    ValidationJobManifest,
    ValidationStageRecord,
)
from ai_quant_os.candidate_validation_job_store import ValidationJobStore
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_jobs import _chain
from ai_quant_os.strategy_prefix_release_contracts import PrefixReleaseDirectory
from ai_quant_os.strategy_prefix_run_contracts import StrategyRunImageInputs

LOCK_NAME = ".validation-job-owner.lock"


class ValidationJobPaused(RuntimeError):
    """Trusted cancellation at a boundary with no reserved or running prefix."""


def prepare_validation_job(
    package, *, root: Path, policy=None, quantity=1, initial_balance=Decimal("100000"), **image_args
) -> ValidationJobManifest:
    package = _checked_package(package)
    root = Path(root).absolute()
    parents = _chain(root)
    args = dict(image_args)
    for name in ("archive", "base_metadata"):
        args[name] = str(Path(args[name]).absolute())
    inputs = StrategyRunImageInputs.model_validate(args)
    folds = (
        package.training.receipt.row_count
        - package.plan.walk_forward_train_bars
        + package.plan.walk_forward_test_bars
        - 1
    ) // package.plan.walk_forward_test_bars
    # Validate the complete declaration before staging any source-only inputs.
    fields = dict(
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        parents=tuple(PrefixReleaseDirectory(path=str(p), identity=i) for p, i in parents),
        image_inputs=inputs,
        development_folds=folds,
        quantity=quantity,
        initial_balance=initial_balance,
    )
    if policy is not None:
        fields["policy"] = policy
    ValidationJobManifest(candidate_test_manifest_sha256="0" * 64, **fields)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        if _chain(root) != parents or {p.name for p in root.iterdir()} != {LOCK_NAME}:
            raise ValueError("New empty ordinary validation root required")
        test_root = root / "candidate-tests"
        test_root.mkdir()
        test_pin = prepare_candidate_tests(package, test_root)
        manifest = ValidationJobManifest(candidate_test_manifest_sha256=test_pin, **fields)
        persist(root / "validation-job.json", manifest.model_dump_json().encode())
        with ValidationJobStore(root, manifest, create=True):
            pass
        return manifest


@contextmanager
def _owned(package, root, job_sha256):
    require_digest(job_sha256, prefix="")
    package = _checked_package(package)
    root = Path(root).absolute()
    parents = _chain(root)
    try:
        raw = read_repair_image_input(root / "validation-job.json", 65536)
    except OSError as error:
        raise ValueError("Original validation job manifest unavailable") from error
    if sha256(raw).hexdigest() != job_sha256:
        raise ValueError("Validation job differs from independent manifest pin")
    manifest = ValidationJobManifest.model_validate_json(raw)
    expected_folds = (
        package.training.receipt.row_count
        - package.plan.walk_forward_train_bars
        + package.plan.walk_forward_test_bars
        - 1
    ) // package.plan.walk_forward_test_bars
    if (
        raw != manifest.model_dump_json().encode()
        or (manifest.package_ref, manifest.candidate_ref, manifest.development_folds)
        != (package.package_ref, package.candidate.candidate_ref, expected_folds)
        or tuple((Path(p.path), p.identity) for p in manifest.parents) != parents
    ):
        raise ValueError("Validation job original package or root changed")
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        if (
            _chain(root) != parents
            or read_repair_image_input(root / "validation-job.json", 65536) != raw
        ):
            raise ValueError("Validation ownership changed")
        job = restore(root / "candidate-tests", manifest.candidate_test_manifest_sha256)
        if job.request != candidate_test_request(package):
            raise ValueError("Original source-only test request changed")
        with ValidationJobStore(root, manifest) as store:
            yield package, root, manifest, store


def _checked_test(package, observed):
    if type(observed) is not CandidateTestResult:
        raise ValueError("Typed original candidate test result required")
    observed = CandidateTestResult.model_validate_json(observed.model_dump_json())
    if observed.status == "PROPOSED_TESTS_PASSED_UNATTESTED":
        return _checked_tests(package, observed)
    record_prerequisite_failure(package, observed)
    return observed


def _publish_checked(path, raw):
    if path.exists():
        if read_repair_image_input(path, 4194304) != raw:
            raise ValueError("Unacknowledged validation observation changed")
    else:
        persist(path, raw)
    return sha256(raw).hexdigest()


def _tests(package, root, manifest, rows):
    record = rows[0]
    raw = read_repair_image_input(root / "test-observation.json", 65536)
    if record.phase != "COMPLETED" or sha256(raw).hexdigest() != record.result_sha256:
        raise ValueError("Original proposed-test acknowledgement required")
    observed = _checked_test(
        package,
        read_candidate_test_result(
            root / "candidate-tests", manifest.candidate_test_manifest_sha256
        ),
    )
    if raw != observed.model_dump_json().encode() or record.test_status != observed.status:
        raise ValueError("Proposed-test observation differs from worker custody")
    return observed


def _run_context(package, root, record, tested):
    if record.stage == "HOLDOUT":
        return package, root / "holdout"
    return development_window(
        package, record.index - 2, tested
    ), root / f"development-{record.index - 2}"


def _run_manifest(context, stage_root, manifest, pin):
    from ai_quant_os.candidate_dataset_run_contracts import run_manifest_model

    require_digest(pin, prefix="")
    raw = read_repair_image_input(stage_root / "strategy-run.json", 65536)
    if sha256(raw).hexdigest() != pin:
        raise ValueError("Original stage manifest pin changed")
    prepared = run_manifest_model(context).model_validate_json(raw)
    if raw != prepared.model_dump_json().encode() or (
        prepared.package_ref,
        prepared.candidate_ref,
        prepared.image_inputs,
        prepared.quantity,
        prepared.initial_balance,
    ) != (
        manifest.package_ref,
        manifest.candidate_ref,
        manifest.image_inputs,
        manifest.quantity,
        manifest.initial_balance,
    ):
        raise ValueError("Stage execution inputs differ from original declaration")
    return prepared


def _report(package, root, manifest, record, tested, *, result_pin=None, manifest_pin=None):
    from ai_quant_os.strategy_prefix_run import recover_strategy_prefix_run_report

    context, stage_root = _run_context(package, root, record, tested)
    pin = manifest_pin or record.manifest_sha256
    _run_manifest(context, stage_root, manifest, pin)
    return recover_strategy_prefix_run_report(
        context, root=stage_root, run_sha256=pin, report_sha256=result_pin or record.result_sha256
    )


def _inputs(package, root, manifest, rows):
    tested = _tests(package, root, manifest, rows)
    if tested.status != "PROPOSED_TESTS_PASSED_UNATTESTED":
        return tested, None, ()
    holdout = _report(package, root, manifest, rows[1], tested)
    windows = tuple(
        _report(package, root, manifest, rows[index], tested)
        for index in range(2, manifest.stage_count - 1)
    )
    return tested, holdout, windows


def _publication(package, root, manifest, rows, archive):
    tested, holdout, windows = _inputs(package, root, manifest, rows)
    if holdout is None:
        return archive.put_failure(package, tested)
    return archive.put_evaluation(package, tested, holdout, windows, policy=manifest.policy)


def _complete(store, record, raw, *, tested=None, artifact=None):
    return store.advance(
        record.model_copy(
            update={
                "phase": "COMPLETED",
                "reason": None,
                "result_sha256": sha256(raw).hexdigest(),
                "test_status": tested.status if tested else None,
                "artifact_ref": (
                    artifact.failure_ref
                    if hasattr(artifact, "failure_ref")
                    else artifact.scorecard_ref
                )
                if artifact is not None
                else None,
            }
        )
    )


def step_validation_job(
    package, *, root: Path, job_sha256: str, archive, checkpoint=None
) -> ValidationStageRecord:
    with _owned(package, root, job_sha256) as (package, root, manifest, store):
        rows = store.snapshot()
        index = store.next_index(rows)
        if index is None:
            return _verify_final(package, root, manifest, rows, archive)
        paused = rows.get(index)
        if paused is not None:
            record = store.advance(
                paused.model_copy(
                    update={"phase": "PREPARED", "reason": None, "attempt": paused.attempt + 1}
                )
            )
        else:
            record = store.advance(
                ValidationStageRecord(
                    index=index, stage=manifest.stage_name(index), phase="RESERVED"
                )
            )
        try:
            if paused is not None:
                pin = record.manifest_sha256
                if record.stage not in ("TESTS", "PUBLICATION"):
                    tested = _tests(package, root, manifest, rows)
                    context, stage_root = _run_context(package, root, record, tested)
                    _run_manifest(context, stage_root, manifest, pin)
            elif record.stage in ("TESTS", "PUBLICATION"):
                pin = manifest.candidate_test_manifest_sha256 if index == 0 else manifest.job_sha256
            else:
                from ai_quant_os.strategy_prefix_run import prepare_strategy_prefix_run

                tested = _tests(package, root, manifest, rows)
                context, stage_root = _run_context(package, root, record, tested)
                stage_root.mkdir()
                pin = prepare_strategy_prefix_run(
                    context,
                    root=stage_root,
                    quantity=manifest.quantity,
                    initial_balance=manifest.initial_balance,
                    **manifest.image_inputs.arguments(),
                ).run_sha256
            record = store.advance(
                record.model_copy(update={"phase": "PREPARED", "manifest_sha256": pin})
            )
            if checkpoint is not None:
                checkpoint()
            if record.stage == "TESTS":
                tested = _checked_test(package, run_candidate_tests(root / "candidate-tests", pin))
                raw = tested.model_dump_json().encode()
                _publish_checked(root / "test-observation.json", raw)
                return _complete(store, record, raw, tested=tested)
            if record.stage == "PUBLICATION":
                artifact = _publication(package, root, manifest, rows, archive)
                raw = artifact.model_dump_json().encode()
                _publish_checked(root / "publication-observation.json", raw)
                return _complete(store, record, raw, artifact=artifact)
            from ai_quant_os.strategy_prefix_run import run_strategy_prefixes

            _run_manifest(context, stage_root, manifest, pin)
            report = run_strategy_prefixes(
                context, root=stage_root, run_sha256=pin, checkpoint=checkpoint
            )
            raw = report.model_dump_json().encode()
            # Verify durable report custody before acknowledging the orchestration stage.
            _report(package, root, manifest, record, tested, result_pin=sha256(raw).hexdigest())
            return _complete(store, record, raw)
        except ValidationJobPaused:
            reason = "COOPERATIVE_STOP_AFTER_OWNED_PREFIX"
            state = "PAUSED"
            current = {**rows, record.index: record}
            if sum(row.attempt for row in current.values()) >= manifest.maximum_cooperative_pauses:
                state, reason = "UNCERTAIN", "STAGE_OUTCOME_UNCONFIRMED"
            return store.advance(record.model_copy(update={"phase": state, "reason": reason}))
        except Exception:
            store.advance(
                record.model_copy(
                    update={"phase": "UNCERTAIN", "reason": "STAGE_OUTCOME_UNCONFIRMED"}
                )
            )
            raise


def validation_job_status(package, *, root: Path, job_sha256: str) -> ValidationStageRecord | None:
    with _owned(package, root, job_sha256) as (_, _, _, store):
        rows = store.snapshot()
        return rows[max(rows)] if rows else None


def read_validation_job_manifest(package, *, root: Path, job_sha256: str) -> ValidationJobManifest:
    with _owned(package, root, job_sha256) as (_, _, manifest, _):
        return manifest


def _verify_final(package, root, manifest, rows, archive):
    record = rows[manifest.stage_count - 1]
    raw = read_repair_image_input(root / "publication-observation.json", 4194304)
    artifact = archive.get(record.artifact_ref)
    if (
        artifact is None
        or artifact.package_ref != package.package_ref
        or artifact.candidate_ref != package.candidate.candidate_ref
        or raw != artifact.model_dump_json().encode()
        or sha256(raw).hexdigest() != record.result_sha256
    ):
        raise ValueError("Checked publication acknowledgement changed")
    return record


def recover_validation_job(
    package, *, root: Path, job_sha256: str, result_sha256: str, archive, stage_manifest_sha256=None
):
    require_digest(result_sha256, prefix="")
    with _owned(package, root, job_sha256) as (package, root, manifest, store):
        rows = store.snapshot()
        if not rows or rows[max(rows)].phase not in ("RESERVED", "PREPARED", "UNCERTAIN"):
            raise ValueError("No unacknowledged validation stage to reconcile")
        record = rows[max(rows)]
        pin = record.manifest_sha256 or stage_manifest_sha256
        if record.stage == "TESTS":
            pin = manifest.candidate_test_manifest_sha256
            tested = _checked_test(
                package, read_candidate_test_result(root / "candidate-tests", pin)
            )
            raw = tested.model_dump_json().encode()
            artifact = None
            target = root / "test-observation.json"
        elif record.stage == "PUBLICATION":
            tested, holdout, windows = _inputs(package, root, manifest, rows)
            if holdout is None:
                expected = record_prerequisite_failure(package, tested)
            else:
                from ai_quant_os.candidate_validation import evaluate_candidate

                expected = evaluate_candidate(
                    package, tested, holdout, windows, policy=manifest.policy
                )
            reference = expected.failure_ref if holdout is None else expected.scorecard_ref
            artifact = archive.get(reference)
            if artifact != expected:
                raise ValueError("Original checked publication must already exist")
            raw = artifact.model_dump_json().encode()
            pin = manifest.job_sha256
            tested = None
            target = root / "publication-observation.json"
        else:
            tested = _tests(package, root, manifest, rows)
            report = _report(
                package, root, manifest, record, tested, result_pin=result_sha256, manifest_pin=pin
            )
            raw = report.model_dump_json().encode()
            tested, artifact, target = None, None, None
        if sha256(raw).hexdigest() != result_sha256 or (
            record.manifest_sha256 is not None and pin != record.manifest_sha256
        ):
            raise ValueError("Recovery differs from independent original-stage pins")
        if target is not None:
            _publish_checked(target, raw)
        if record.phase == "RESERVED":
            record = store.advance(
                record.model_copy(update={"phase": "PREPARED", "manifest_sha256": pin})
            )
        else:
            record = record.model_copy(update={"manifest_sha256": pin})
        return _complete(store, record, raw, tested=tested, artifact=artifact)
