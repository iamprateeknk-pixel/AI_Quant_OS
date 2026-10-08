"""Read-only capture and historical verification of original stage uncertainty."""

from ai_quant_os.candidate_validation_job import _owned
from ai_quant_os.validation_interruption_contracts import (
    ValidationInterruption,
    interruption_boundary,
)


def capture_validation_interruption(package, *, root, job_sha256) -> ValidationInterruption:
    with _owned(package, root, job_sha256) as (package, _, manifest, store):
        rows, integrity = store.audit_checkpoint()
        if not rows:
            raise ValueError("No reserved original validation stage exists")
        stage = rows[max(rows)]
        return ValidationInterruption(
            package_ref=package.package_ref,
            candidate_ref=package.candidate.candidate_ref,
            job_sha256=manifest.job_sha256,
            stage=stage,
            audit_head_sha256=integrity.head_hash,
            audit_event_count=integrity.event_count,
            boundary=interruption_boundary(stage.stage),
        )


def verify_validation_interruption(package, *, root, job_sha256, record) -> None:
    if type(record) is not ValidationInterruption or set(record.model_fields_set) - set(
        ValidationInterruption.model_fields
    ):
        raise ValueError("Exact typed interruption observation required")
    record = ValidationInterruption.model_validate_json(record.model_dump_json())
    with _owned(package, root, job_sha256) as (package, _, manifest, store):
        if (record.package_ref, record.candidate_ref, record.job_sha256) != (
            package.package_ref,
            package.candidate.candidate_ref,
            manifest.job_sha256,
        ):
            raise ValueError("Interruption differs from original package and job")
        store.require_audit_checkpoint(
            record.stage, record.audit_event_count, record.audit_head_sha256
        )
