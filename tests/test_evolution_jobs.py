from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

import pytest

from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_catalog import EvolutionCatalog
from ai_quant_os.genome_store import GenomeStore
from test_agent_genomes import child
from test_evolution_protocols import dataset, protocol


def retained_training_pair(tmp_path):
    registry = load_agent_catalog()
    data = dataset()
    plan = protocol(data)
    catalog = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    baseline = capture_baseline_genome(registry, "A02", plan.objective)
    challenger = child(baseline)
    genomes = GenomeStore(tmp_path / "genomes.sqlite3", registry=registry)
    genomes.register(baseline)
    genomes.register(challenger)
    return catalog, genomes, plan, baseline, challenger


def test_training_job_enqueue_is_idempotent_and_restart_visible(tmp_path):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    queue = EvolutionJobQueue(catalog, genomes, clock=lambda: now)

    first = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    duplicate = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    restarted = EvolutionJobQueue(catalog, genomes, clock=lambda: now)

    assert first == duplicate
    assert first.job_ref.startswith("evolution-job:")
    assert first.state == "PENDING"
    assert first.protocol_sha256 == plan.protocol_sha256
    assert first.dataset_ref == plan.dataset_ref
    assert first.baseline_genome_ref == baseline.genome_ref
    assert first.challenger_genome_ref == challenger.genome_ref
    assert first.evaluation_verified is False
    assert first.promotion_allowed is False
    assert restarted.list_jobs() == (first,)


def test_job_output_matches_checked_in_portable_schema(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from ai_quant_os.evolution_jobs import EvolutionJobQueue, evolution_training_job_schema

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    queue = EvolutionJobQueue(
        catalog, genomes, clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    )
    job = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    schema = evolution_training_job_schema()
    path = Path(__file__).resolve().parents[1] / "data/schemas/evolution-training-job.schema.json"

    assert json.loads(path.read_text(encoding="utf-8")) == schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(job.model_dump(mode="json"))


def test_training_job_refuses_holdout_or_non_direct_genome_pair(tmp_path):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    queue = EvolutionJobQueue(
        catalog, genomes, clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    )

    with pytest.raises(ValueError, match="parent, version"):
        queue.enqueue(plan.protocol_sha256, baseline.genome_ref, baseline.genome_ref)

    holdout_root = tmp_path / "holdout"
    holdout_data = dataset().model_copy(update={"resource_class": "HOLDOUT"})
    holdout_plan = protocol(holdout_data)
    holdout_catalog = EvolutionCatalog(holdout_root / "evaluation.sqlite3")
    holdout_catalog.register(holdout_data)
    holdout_catalog.register(holdout_plan)
    holdout_baseline = capture_baseline_genome(load_agent_catalog(), "A02", holdout_plan.objective)
    holdout_challenger = child(holdout_baseline)
    holdout_genomes = GenomeStore(holdout_root / "genomes.sqlite3", registry=load_agent_catalog())
    holdout_genomes.register(holdout_baseline)
    holdout_genomes.register(holdout_challenger)
    holdout_queue = EvolutionJobQueue(
        holdout_catalog,
        holdout_genomes,
        clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
    )
    with pytest.raises(PermissionError, match="tool access denied"):
        holdout_queue.enqueue(
            holdout_plan.protocol_sha256,
            holdout_baseline.genome_ref,
            holdout_challenger.genome_ref,
        )

    assert queue.list_jobs() == ()


def test_concurrent_identical_training_job_submissions_share_one_audited_job(tmp_path):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)

    def now():
        return datetime(2026, 10, 19, tzinfo=timezone.utc)

    queues = tuple(EvolutionJobQueue(catalog, genomes, clock=now) for _ in range(2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = tuple(
            pool.map(
                lambda queue: queue.enqueue(
                    plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref
                ),
                queues,
            )
        )

    assert jobs[0] == jobs[1]
    assert queues[0].list_jobs() == (jobs[0],)
    with closing(sqlite3.connect(queues[0].path)) as db:
        assert db.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1


def test_job_queue_refuses_corrupt_audit_history_on_restart(tmp_path):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    queue = EvolutionJobQueue(
        catalog, genomes, clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    )
    queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    with closing(sqlite3.connect(queue.path)) as db:
        db.execute("UPDATE job_events SET event_json='{}'")
        db.commit()

    with pytest.raises(ValueError, match="audit chain"):
        EvolutionJobQueue(
            catalog, genomes, clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
        )


def test_worker_runs_one_claimed_job_and_retains_only_unverified_scorecard(tmp_path, monkeypatch):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue
    from ai_quant_os.evolution_runner import EvolutionRunner, GenomeExecutionResult

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)

    def now():
        return datetime(2026, 10, 19, tzinfo=timezone.utc)

    queue = EvolutionJobQueue(catalog, genomes, clock=now)
    queued = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    calls = []

    def fake_run(self, genome, data, protocol, *, baseline=None):
        calls.append(genome.genome_ref)
        return GenomeExecutionResult(
            genome_ref=genome.genome_ref,
            dataset_ref=data.dataset_ref,
            outputs=({"case_id": data.cases[0].case_id, "output": {"regime": "TRENDING"}},),
        )

    monkeypatch.setattr(EvolutionRunner, "run", fake_run)
    completed = queue.process_next()
    restarted = EvolutionJobQueue(catalog, genomes, clock=now)

    assert calls == [baseline.genome_ref, challenger.genome_ref]
    assert completed.job_ref == queued.job_ref
    assert completed.state == "COMPLETED"
    assert completed.scorecard_ref.startswith("evolution-scorecard:")
    assert completed.evaluation_verified is False
    assert completed.promotion_allowed is False
    assert restarted.list_jobs() == (completed,)
    scorecard = catalog.scorecard(completed.scorecard_ref)
    assert scorecard is not None
    assert scorecard.evaluation_verified is False
    assert scorecard.promotion_allowed is False


def test_claim_is_atomic_and_interrupted_running_job_is_not_replayed(tmp_path):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)

    def now():
        return datetime(2026, 10, 19, tzinfo=timezone.utc)

    queues = tuple(EvolutionJobQueue(catalog, genomes, clock=now) for _ in range(2))
    queued = queues[0].enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(pool.map(lambda queue: queue.claim_next(), queues))

    claimed = tuple(job for job in claims if job is not None)
    assert len(claimed) == 1
    assert claimed[0].job_ref == queued.job_ref
    assert claimed[0].state == "RUNNING"

    restarted = EvolutionJobQueue(catalog, genomes, clock=now)
    assert restarted.process_next() is None
    assert restarted.list_jobs()[0].state == "RUNNING"


def test_failed_training_job_records_only_sanitized_error_and_is_not_retried(tmp_path, monkeypatch):
    from ai_quant_os.evolution_jobs import EvolutionJobQueue
    from ai_quant_os.evolution_runner import EvolutionRunner

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)

    def now():
        return datetime(2026, 10, 19, tzinfo=timezone.utc)

    queue = EvolutionJobQueue(catalog, genomes, clock=now)
    queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)

    def fail_run(self, genome, data, protocol, *, baseline=None):
        raise RuntimeError("private worker detail must not enter the job journal")

    monkeypatch.setattr(EvolutionRunner, "run", fail_run)
    failed = queue.process_next()
    restarted = EvolutionJobQueue(catalog, genomes, clock=now)

    assert failed.state == "FAILED"
    assert failed.failure_code == "TRAINING_EXECUTION_FAILED"
    assert "private worker detail" not in failed.model_dump_json()
    assert restarted.process_next() is None


def test_interrupted_job_recovery_requires_worker_stop_and_exact_revision(tmp_path):
    from datetime import timedelta
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)

    def now():
        return datetime(2026, 10, 19, tzinfo=timezone.utc)

    queue = EvolutionJobQueue(catalog, genomes, clock=now)
    queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    running = queue.claim_next()

    with pytest.raises(ValueError, match="confirm the prior worker stopped"):
        queue.recover_running(
            running.job_ref,
            expected_updated_at=running.updated_at,
            recovery_id="recovery-one",
            reason="Prior scheduler process confirmed stopped",
            worker_stopped=False,
        )
    with pytest.raises(ValueError, match="revision"):
        queue.recover_running(
            running.job_ref,
            expected_updated_at=running.updated_at - timedelta(seconds=1),
            recovery_id="recovery-one",
            reason="Prior scheduler process confirmed stopped",
            worker_stopped=True,
        )

    recovered = queue.recover_running(
        running.job_ref,
        expected_updated_at=running.updated_at,
        recovery_id="recovery-one",
        reason="Prior scheduler process confirmed stopped",
        worker_stopped=True,
    )
    retried = queue.recover_running(
        running.job_ref,
        expected_updated_at=running.updated_at,
        recovery_id="recovery-one",
        reason="Prior scheduler process confirmed stopped",
        worker_stopped=True,
    )
    restarted = EvolutionJobQueue(catalog, genomes, clock=now)

    assert recovered == retried
    assert recovered.state == "PENDING"
    assert recovered.recovery_count == 1
    assert recovered.recovery_id == "recovery-one"
    assert recovered.evaluation_verified is False
    assert recovered.promotion_allowed is False
    assert restarted.list_jobs() == (recovered,)


def test_interrupted_job_recovery_has_a_bounded_retry_count(tmp_path):
    from datetime import timedelta
    from ai_quant_os.evolution_jobs import EvolutionJobQueue

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    now = [datetime(2026, 10, 19, tzinfo=timezone.utc)]

    def clock():
        return now[0]

    queue = EvolutionJobQueue(catalog, genomes, clock=clock)
    queued = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    for attempt in range(3):
        running = queue.claim_next()
        queue.recover_running(
            queued.job_ref,
            expected_updated_at=running.updated_at,
            recovery_id=f"recovery-{attempt}",
            reason="Prior scheduler process confirmed stopped",
            worker_stopped=True,
        )
        now[0] += timedelta(seconds=1)
    running = queue.claim_next()

    with pytest.raises(ValueError, match="recovery limit"):
        queue.recover_running(
            queued.job_ref,
            expected_updated_at=running.updated_at,
            recovery_id="recovery-final",
            reason="Prior scheduler process confirmed stopped",
            worker_stopped=True,
        )
