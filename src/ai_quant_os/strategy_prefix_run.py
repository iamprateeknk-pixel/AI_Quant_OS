"""Durable serial prefix reservations, owned execution/release and native replay."""

from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

from ai_quant_os.candidate_invocations import _inputs, _invocation
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_container_lifecycle import (
    prepare_strategy_container,
    create_strategy_container,
)
from ai_quant_os.strategy_execution_tape import (
    assemble_strategy_execution_tape,
    replay_strategy_execution_tape,
    verify_strategy_execution_replay,
)
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_image_loading import inspect_strategy_worker_image
from ai_quant_os.strategy_prefix_execution import execute_strategy_prefix
from ai_quant_os.strategy_prefix_jobs import stage_prefix_job, _chain
from ai_quant_os.strategy_prefix_release import (
    prepare_strategy_prefix_release,
    release_strategy_prefix_inputs,
)
from ai_quant_os.strategy_prefix_release_binding import require_release_package
from ai_quant_os.strategy_prefix_release_contracts import (
    PrefixReleaseDirectory,
    StrategyPrefixReleaseBundle,
)
from ai_quant_os.strategy_prefix_run_contracts import (
    StrategyRunImageInputs,
    StrategyPrefixRunManifest,
    StrategyRunPrefix,
    StrategyPrefixRunReport,
)
from ai_quant_os.strategy_prefix_run_store import StrategyPrefixRunStore
from ai_quant_os.validation_package_contracts import ValidationPackage

RUN_LOCK = ".strategy-run.lock"
MAX_REPORT = 134217728


def _publish(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _same_image(manifest, fresh):
    if (
        fresh.archive != manifest.handoff.archive
        or fresh.engine_id != manifest.handoff.engine_id
        or fresh.image_metadata_sha256 != manifest.handoff.image_metadata_sha256
    ):
        raise ValueError("Run image/engine/source admission changed")


def prepare_strategy_prefix_run(
    package: ValidationPackage,
    *,
    root: Path,
    quantity: int = 1,
    initial_balance: Decimal = Decimal("100000"),
    **image_args,
) -> StrategyPrefixRunManifest:
    package, dataset = _inputs(package)
    parents = _chain(root)
    parent = parents[-1][0]
    if any(parent.iterdir()):
        raise ValueError("A new empty ordinary run root is required")
    args = dict(image_args)
    args["archive"] = Path(args["archive"]).absolute()
    args["base_metadata"] = Path(args["base_metadata"]).absolute()
    handoff = inspect_strategy_worker_image(**args)
    inputs = StrategyRunImageInputs.model_validate(
        {key: str(value) if isinstance(value, Path) else value for key, value in args.items()}
    )
    manifest = StrategyPrefixRunManifest(
        run_id=uuid4().hex,
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        row_count=len(dataset.bars),
        parents=tuple(
            PrefixReleaseDirectory(path=str(path), identity=identity) for path, identity in parents
        ),
        image_inputs=inputs,
        handoff=handoff,
        quantity=quantity,
        initial_balance=initial_balance,
    )
    raw = manifest.model_dump_json().encode()
    if len(raw) > 65536:
        raise ValueError("Run manifest exceeds byte bound")
    with kernel_root_lock(parent, lock_name=RUN_LOCK):
        if _chain(parent) != parents or {path.name for path in parent.iterdir()} != {RUN_LOCK}:
            raise ValueError("Run root changed before initial publication")
        _publish(parent / "strategy-run.json", raw)
        with StrategyPrefixRunStore(parent, manifest, create=True):
            pass
    return manifest


@contextmanager
def _owned_run(package, root, run_sha256):
    require_digest(run_sha256, prefix="")
    package, dataset = _inputs(package)
    parents = _chain(root)
    parent = parents[-1][0]
    raw = read_repair_image_input(parent / "strategy-run.json", 65536)
    if sha256(raw).hexdigest() != run_sha256:
        raise ValueError("Run manifest differs from independent pin")
    manifest = StrategyPrefixRunManifest.model_validate_json(raw)
    if (
        raw != manifest.model_dump_json().encode()
        or tuple((Path(row.path), row.identity) for row in manifest.parents) != parents
        or (manifest.package_ref, manifest.candidate_ref, manifest.row_count)
        != (package.package_ref, package.candidate.candidate_ref, len(dataset.bars))
    ):
        raise ValueError("Run manifest differs from original package/root")
    with kernel_root_lock(parent, lock_name=RUN_LOCK):
        if (
            _chain(parent) != parents
            or read_repair_image_input(parent / "strategy-run.json", 65536) != raw
        ):
            raise ValueError("Run root/manifest changed before ownership")
        with StrategyPrefixRunStore(parent, manifest) as store:
            expected = tuple(
                _invocation(
                    package, dataset, index, manifest.package_ref, manifest.candidate_ref
                ).invocation_ref
                for index in range(2, manifest.row_count)
            )
            records, final = store.snapshot()
            for record in records.values():
                if record.invocation_ref != expected[record.index - 2]:
                    raise ValueError("Run reservation differs from original source/history")
            _root_inventory(parent, records)
            yield package, manifest, store, expected
            if (
                _chain(parent) != parents
                or read_repair_image_input(parent / "strategy-run.json", 65536) != raw
            ):
                raise ValueError("Run root/manifest changed during operation")


def _root_inventory(root, records):
    names = {record.job_name for record in records.values()}
    for path in root.iterdir():
        if path.name.startswith("strategy-job-") and path.name.split(".", 1)[0] not in names:
            raise ValueError("Untracked prefix custody prevents run continuation")


def _released_record(package, root, record, manifest):
    path = root / (record.job_name + ".release.json")
    raw = read_repair_image_input(path, 2097152)
    if sha256(raw).hexdigest() != record.release_sha256:
        raise ValueError("Run recovery archive differs from acknowledged pin")
    bundle = StrategyPrefixReleaseBundle.model_validate_json(raw)
    require_release_package(bundle, package)
    if (
        bundle.index != record.index
        or bundle.job_name != record.job_name
        or bundle.receipt != record.receipt
    ):
        raise ValueError("Run recovery archive differs from reserved prefix completion")
    release_strategy_prefix_inputs(
        package, root=root, job_name=record.job_name, release_sha256=record.release_sha256
    )
    return len(raw)


def _collection(package, manifest, records):
    return assemble_strategy_execution_tape(
        package,
        tuple(row.receipt for row in records.values()),
        image_id=manifest.handoff.loaded_image_id,
        worker_sha256=manifest.image_inputs.worker_sha256,
    )


def _checked_report(package, manifest, records, raw):
    report = StrategyPrefixRunReport.model_validate_json(raw)
    if (
        raw != report.model_dump_json().encode()
        or report.manifest != manifest
        or report.replay.collection != _collection(package, manifest, records)
    ):
        raise ValueError("Full run report differs from original manifest/acknowledged inventory")
    verify_strategy_execution_replay(package, report.replay)
    verify_strategy_execution_replay(package, report.stress_replay)
    return report


def run_strategy_prefixes(
    package: ValidationPackage, *, root: Path, run_sha256: str
) -> StrategyPrefixRunReport:
    with _owned_run(package, root, run_sha256) as (package, manifest, store, expected):
        parent = store.path.parent
        records, final = store.snapshot()
        if any(record.phase != "COMPLETED" for record in records.values()):
            raise ValueError("Uncertain reserved prefix requires explicit pinned reconciliation")
        retained = sum(
            _released_record(package, parent, record, manifest) for record in records.values()
        )
        if retained > manifest.maximum_recovery_bytes:
            raise ValueError("Run recovery retention ceiling exceeded")
        report_path = parent / "strategy-run-replay.json"
        if final is not None:
            if final["state"] != "COMPLETED" and report_path.exists():
                raise ValueError("Reserved replay requires independently pinned report recovery")
            if final["state"] == "COMPLETED":
                raw = read_repair_image_input(report_path, MAX_REPORT)
                if sha256(raw).hexdigest() != final["report_sha256"]:
                    raise ValueError("Run report differs from audited acknowledgement")
                return _checked_report(package, manifest, records, raw)
        if report_path.exists():
            raise ValueError("Unacknowledged run report cannot be overwritten")
        if len(records) < manifest.row_count - 2:
            _same_image(
                manifest, inspect_strategy_worker_image(**manifest.image_inputs.arguments())
            )
        for index in range(len(records) + 2, manifest.row_count):
            record = StrategyRunPrefix(
                index=index,
                job_name="strategy-job-" + uuid4().hex,
                invocation_ref=expected[index - 2],
                phase="RESERVED",
            )
            store.advance(record)
            job = stage_prefix_job(package, index, root=parent, job_name=record.job_name)
            record = record.model_copy(update={"phase": "STAGED", "stage_sha256": job.stage_sha256})
            store.advance(record)
            plan = prepare_strategy_container(job, package, **manifest.image_inputs.arguments())
            _same_image(manifest, plan.handoff)
            create_strategy_container(plan, job, package, **manifest.image_inputs.arguments())
            receipt = execute_strategy_prefix(
                plan, job, package, **manifest.image_inputs.arguments()
            )
            bundle = prepare_strategy_prefix_release(
                plan,
                job,
                package,
                receipt_sha256=sha256(receipt.model_dump_json().encode()).hexdigest(),
            )
            retained += len(bundle.model_dump_json().encode())
            if retained > manifest.maximum_recovery_bytes:
                raise ValueError("Run recovery retention ceiling exceeded")
            record = record.model_copy(
                update={
                    "phase": "COMPLETED",
                    "receipt": receipt,
                    "release_sha256": bundle.release_sha256,
                }
            )
            store.advance(record)
            records[index] = record
            release_strategy_prefix_inputs(
                package, root=parent, job_name=job.path.name, release_sha256=bundle.release_sha256
            )
        if final is None:
            store.reserve_replay()
        collection = _collection(package, manifest, records)
        parameters = {"quantity": manifest.quantity, "initial_balance": manifest.initial_balance}
        report = StrategyPrefixRunReport(
            manifest=manifest,
            replay=replay_strategy_execution_tape(package, collection, **parameters),
            stress_replay=replay_strategy_execution_tape(
                package, collection, cost_scenario="DECLARED_STRESS", **parameters
            ),
        )
        raw = report.model_dump_json().encode()
        if len(raw) > MAX_REPORT:
            raise ValueError("Full run report exceeds byte bound")
        _publish(report_path, raw)
        store.complete(sha256(raw).hexdigest())
        return report


def recover_strategy_prefix_run(
    package: ValidationPackage, *, root: Path, run_sha256: str, release_sha256: str
) -> StrategyRunPrefix:
    require_digest(release_sha256, prefix="")
    with _owned_run(package, root, run_sha256) as (package, manifest, store, expected):
        records, final = store.snapshot()
        if final is not None or not records:
            raise ValueError("No unfinished run prefix to reconcile")
        record = records[max(records)]
        raw = read_repair_image_input(
            store.path.parent / (record.job_name + ".release.json"), 2097152
        )
        if sha256(raw).hexdigest() != release_sha256:
            raise ValueError("Recovery differs from independent archive pin")
        bundle = StrategyPrefixReleaseBundle.model_validate_json(raw)
        require_release_package(bundle, package)
        if (
            (bundle.index, bundle.job_name, bundle.receipt.target.invocation_ref)
            != (record.index, record.job_name, record.invocation_ref)
            or bundle.receipt.ownership.image_id != manifest.handoff.loaded_image_id
            or bundle.receipt.ownership.worker_sha256 != manifest.image_inputs.worker_sha256
            or record.stage_sha256 not in (None, bundle.receipt.ownership.stage_sha256)
        ):
            raise ValueError("Recovery archive differs from original run reservation")
        release_strategy_prefix_inputs(
            package, root=store.path.parent, job_name=record.job_name, release_sha256=release_sha256
        )
        if record.phase == "RESERVED":
            record = record.model_copy(
                update={"phase": "STAGED", "stage_sha256": bundle.receipt.ownership.stage_sha256}
            )
            store.advance(record)
        completed = record.model_copy(
            update={
                "phase": "COMPLETED",
                "receipt": bundle.receipt,
                "release_sha256": release_sha256,
            }
        )
        store.advance(completed)
        return completed


def recover_strategy_prefix_run_report(
    package: ValidationPackage, *, root: Path, run_sha256: str, report_sha256: str
) -> StrategyPrefixRunReport:
    require_digest(report_sha256, prefix="")
    with _owned_run(package, root, run_sha256) as (package, manifest, store, expected):
        records, final = store.snapshot()
        if final != {"state": "REPLAY_RESERVED"} and final != {
            "state": "COMPLETED",
            "report_sha256": report_sha256,
        }:
            raise ValueError("Run has no matching reserved report to recover")
        for record in records.values():
            _released_record(package, store.path.parent, record, manifest)
        path = store.path.parent / "strategy-run-replay.json"
        raw = read_repair_image_input(path, MAX_REPORT)
        if sha256(raw).hexdigest() != report_sha256:
            raise ValueError("Run report differs from independent recovery pin")
        report = _checked_report(package, manifest, records, raw)
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())
        if read_repair_image_input(path, MAX_REPORT) != raw:
            raise ValueError("Run report changed during recovery sync")
        store.complete(report_sha256)
        return report
