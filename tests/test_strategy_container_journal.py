"""Owned durable metadata and simulated acknowledgements; no Docker action."""

from hashlib import sha256
import importlib
import json
from pathlib import Path
import sqlite3

import pytest
import jsonschema

from ai_quant_os.strategy_prefix_jobs import stage_prefix_job
from test_strategy_prefix_worker import package as package


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_container_journal"), (
        "Durable strategy container ownership is required"
    )
    return importlib.import_module("ai_quant_os.strategy_container_journal")


@pytest.fixture
def job(package, tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    return stage_prefix_job(package, 2, root=root)


def reserve(job, package):
    return module().reserve_strategy_container(
        job, package, image_id="sha256:" + "1" * 64, worker_sha256="2" * 64
    )


def test_ownership_is_published_and_audited_before_any_container_rpc(job, package):
    ownership = reserve(job, package)
    assert ownership.job_name == job.path.name and ownership.invocation_ref == job.invocation_ref
    assert ownership.stage_sha256 == job.stage_sha256
    assert ownership.container_name == "ai-quant-strategy-" + ownership.owner
    assert ownership.execution_verified is False and ownership.promotion_allowed is False
    path = job.path.parent / (job.path.name + ".container.json")
    assert path.is_file()
    assert path.read_bytes() == ownership.model_dump_json().encode()
    with module().StrategyContainerJournal(job.path.parent) as journal:
        state = journal.current(ownership)
        assert state.state == "CREATE_INTENT" and state.container_id == ""
        assert journal.event_count == 1
    with pytest.raises(ValueError):
        job.discard_unstarted(package)
    assert {path.name for path in job.path.iterdir()} == {"request.json"}


def test_exact_create_and_removal_history_survives_restart(job, package):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        journal.acknowledge_create(ownership, "3" * 64)
        journal.removal_intent(ownership, "3" * 64)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        journal.acknowledge_removal(ownership, "3" * 64)
        assert journal.current(ownership).state == "REMOVED"
        assert journal.event_count == 4
    assert (job.path / "request.json").is_file()
    assert (job.path.parent / (job.path.name + ".container.json")).is_file()
    with pytest.raises(ValueError):
        job.discard_unstarted(package)


def test_ambiguous_create_can_record_inspected_owned_id_for_cleanup_without_create_retry(
    job, package
):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        journal.removal_intent(ownership, "3" * 64)
        journal.acknowledge_removal(ownership, "3" * 64)
        assert journal.event_count == 3
        with pytest.raises(ValueError):
            journal.acknowledge_create(ownership, "3" * 64)


@pytest.mark.parametrize("operation", ["create_ack", "removal_intent", "removal_ack"])
def test_acknowledgements_require_prior_owned_intent(job, package, operation):
    ownership = module().StrategyContainerOwnership(
        owner="4" * 32,
        job_path=str(job.path),
        job_name=job.path.name,
        invocation_ref=job.invocation_ref,
        stage_sha256=job.stage_sha256,
        image_id="sha256:" + "1" * 64,
        worker_sha256="2" * 64,
    )
    with module().StrategyContainerJournal(job.path.parent) as journal:
        action = {
            "create_ack": journal.acknowledge_create,
            "removal_intent": journal.removal_intent,
            "removal_ack": journal.acknowledge_removal,
        }[operation]
        with pytest.raises(ValueError):
            action(ownership, "3" * 64)
        assert journal.event_count == 0


@pytest.mark.parametrize(
    "field",
    [
        "owner",
        "job_path",
        "job_name",
        "invocation_ref",
        "stage_sha256",
        "image_id",
        "worker_sha256",
    ],
)
def test_mutated_binding_cannot_acknowledge_existing_owner(job, package, field):
    ownership = reserve(job, package)
    payload = json.loads(ownership.model_dump_json())
    if field == "owner":
        payload[field] = "4" * 32
    elif field == "job_path":
        payload[field] = str(job.path.with_name("strategy-job-" + "4" * 32))
    elif field == "job_name":
        payload[field] = "strategy-job-" + "4" * 32
    elif field == "invocation_ref":
        payload[field] = "candidate-invocation:" + "4" * 64
    elif field == "image_id":
        payload[field] = "sha256:" + "4" * 64
    else:
        payload[field] = "4" * 64
    with pytest.raises(ValueError):
        changed = type(ownership).model_validate(payload)
        with module().StrategyContainerJournal(job.path.parent) as journal:
            journal.acknowledge_create(changed, "3" * 64)


@pytest.mark.parametrize("container", ["", "short", "sha256:" + "3" * 64, True, 1, "F" * 64])
def test_invalid_container_ids_do_not_append_history(job, package, container):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        with pytest.raises(ValueError):
            journal.acknowledge_create(ownership, container)
        assert journal.event_count == 1


def test_conflicting_or_out_of_order_acknowledgement_is_refused(job, package):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        with pytest.raises(ValueError):
            journal.acknowledge_removal(ownership, "3" * 64)
        journal.acknowledge_create(ownership, "3" * 64)
        with pytest.raises(ValueError):
            journal.removal_intent(ownership, "4" * 64)
        journal.removal_intent(ownership, "3" * 64)
        with pytest.raises(ValueError):
            journal.acknowledge_removal(ownership, "4" * 64)
        assert journal.event_count == 3


def test_exact_acknowledgements_are_idempotent_without_reissuing_actions(job, package):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        journal.acknowledge_create(ownership, "3" * 64)
        journal.acknowledge_create(ownership, "3" * 64)
        journal.removal_intent(ownership, "3" * 64)
        journal.removal_intent(ownership, "3" * 64)
        journal.acknowledge_removal(ownership, "3" * 64)
        journal.acknowledge_removal(ownership, "3" * 64)
        assert journal.event_count == 4


def test_second_reservation_for_same_job_refuses_and_keeps_original_sidecar(job, package):
    ownership = reserve(job, package)
    raw = (job.path.parent / (job.path.name + ".container.json")).read_bytes()
    with pytest.raises((ValueError, FileExistsError)):
        reserve(job, package)
    assert raw == ownership.model_dump_json().encode()
    assert (job.path.parent / (job.path.name + ".container.json")).read_bytes() == raw


def test_damaged_journal_is_not_reinitialized_or_used_for_acknowledgement(job, package):
    reserve(job, package)
    path = job.path.parent / "strategy-containers.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_events SET event_json='{}'")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        module().StrategyContainerJournal(job.path.parent)
    assert path.read_bytes() == before
    assert (job.path.parent / (job.path.name + ".container.json")).exists()


def test_missing_integrity_head_refuses_adoption_of_existing_events(job, package):
    reserve(job, package)
    path = job.path.parent / "strategy-containers.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_head")
    with pytest.raises(ValueError):
        module().StrategyContainerJournal(job.path.parent)


def test_journal_write_failure_retains_ownership_sidecar_and_request(job, package, monkeypatch):
    implementation = module()

    def fail(*args, **kwargs):
        raise ValueError("simulated commit failure")

    monkeypatch.setattr(implementation.StrategyContainerJournal, "reserve", fail)
    with pytest.raises(ValueError):
        reserve(job, package)
    assert (job.path.parent / (job.path.name + ".container.json")).exists()
    with pytest.raises(ValueError):
        job.discard_unstarted(package)


def test_ownership_model_refuses_execution_and_promotion_claims(job, package):
    ownership = reserve(job, package)
    payload = json.loads(ownership.model_dump_json())
    for field in ("execution_verified", "promotion_allowed"):
        with pytest.raises(ValueError):
            type(ownership).model_validate({**payload, field: True})


def test_corrupt_owned_sidecar_is_retained_and_cannot_be_loaded(job, package):
    reserve(job, package)
    path = job.path.parent / (job.path.name + ".container.json")
    raw = path.read_bytes()
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        module().load_strategy_container_ownership(
            job, package, ownership_sha256=sha256(raw).hexdigest()
        )
    assert path.read_bytes() == b"changed"


def test_independent_ownership_pin_restores_bound_create_intent(job, package):
    ownership = reserve(job, package)
    raw = (job.path.parent / (job.path.name + ".container.json")).read_bytes()
    restored = module().load_strategy_container_ownership(
        job, package, ownership_sha256=sha256(raw).hexdigest()
    )
    assert restored == ownership


def test_different_owner_cannot_reserve_the_same_job_in_the_journal(job, package):
    ownership = reserve(job, package)
    second = type(ownership).model_validate({**ownership.model_dump(), "owner": "4" * 32})
    with module().StrategyContainerJournal(job.path.parent) as journal:
        with pytest.raises(ValueError):
            journal.reserve(second)
        assert journal.event_count == 1


def test_semantically_invalid_rehashed_history_is_rejected(job, package):
    from ai_quant_os.audit_chain import AuditChain

    ownership = reserve(job, package)
    second = type(ownership).model_validate({**ownership.model_dump(), "owner": "4" * 32})
    record = module().StrategyContainerRecord(
        ownership=second, state="CREATE_INTENT", container_id=""
    )
    path = job.path.parent / "strategy-containers.sqlite3"
    with sqlite3.connect(path) as connection:
        AuditChain(connection).append(
            "strategy-container:" + second.owner + ":CREATE_INTENT", record.model_dump(mode="json")
        )
    with pytest.raises(ValueError):
        module().StrategyContainerJournal(job.path.parent)


def test_bounded_capacity_refuses_another_owner_without_changing_history(job, package):
    ownership = reserve(job, package)
    second = type(ownership).model_validate(
        {
            **ownership.model_dump(),
            "owner": "4" * 32,
            "job_name": "strategy-job-" + "4" * 32,
            "job_path": str(job.path.with_name("strategy-job-" + "4" * 32)),
        }
    )
    with module().StrategyContainerJournal(job.path.parent, capacity=1) as journal:
        with pytest.raises(ValueError):
            journal.reserve(second)
        assert journal.event_count == 1


def test_retained_wire_schemas_match_models_and_refuse_execution_authority(job, package):
    ownership = reserve(job, package)
    with module().StrategyContainerJournal(job.path.parent) as journal:
        record = journal.current(ownership)
    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    for name, value in (
        ("strategy-container-ownership", ownership),
        ("strategy-container-record", record),
    ):
        path = root / (name + ".schema.json")
        assert path.is_file(), "Container ownership wire schema is required"
        schema = json.loads(path.read_bytes())
        assert schema == type(value).model_json_schema()
        payload = value.model_dump(mode="json")
        jsonschema.validate(payload, schema)
        for field in ("execution_verified", "promotion_allowed"):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate({**payload, field: True}, schema)


@pytest.mark.parametrize("value", [None, "job", {}])
def test_unprepared_input_is_refused_before_ownership_access(package, value):
    with pytest.raises(ValueError):
        module().load_strategy_container_ownership(value, package, ownership_sha256="0" * 64)


def test_corrupt_integrity_field_type_is_a_controlled_refusal(job, package):
    reserve(job, package)
    path = job.path.parent / "strategy-containers.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_head SET event_count='invalid'")
    with pytest.raises(ValueError):
        module().StrategyContainerJournal(job.path.parent)


def test_encoded_event_bound_is_enforced_before_commit(job):
    # Synthetic path metadata isolates JSON escaping, not filesystem admission.
    virtual_root = job.path.parent / ("é" * 1400)
    ownership = module().StrategyContainerOwnership(
        owner="4" * 32,
        job_path=str(virtual_root / job.path.name),
        job_name=job.path.name,
        invocation_ref=job.invocation_ref,
        stage_sha256=job.stage_sha256,
        image_id="sha256:" + "1" * 64,
        worker_sha256="2" * 64,
    )
    record = module().StrategyContainerRecord(
        ownership=ownership, state="CREATE_INTENT", container_id=""
    )
    assert len(json.dumps(record.model_dump(mode="json"), sort_keys=True).encode()) > 8192
    with module().StrategyContainerJournal(job.path.parent) as journal:
        journal.root = virtual_root
        with pytest.raises(ValueError):
            journal.reserve(ownership)
        assert journal.event_count == 0
