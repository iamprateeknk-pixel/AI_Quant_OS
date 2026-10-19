from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_catalog import EvolutionCatalog
from ai_quant_os.evolution_evaluation import CaseOutput, evaluate_protocol_outputs
from ai_quant_os.api import create_app
from fastapi.testclient import TestClient
from test_agent_genomes import child
from test_evolution_protocols import dataset, protocol


def retained_scorecard(workspace, *, evaluated_at):
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    catalog = EvolutionCatalog(workspace / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    catalog.register(
        evaluate_protocol_outputs(
            plan,
            data,
            baseline,
            challenger,
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=evaluated_at,
        )
    )
    return catalog, plan


def test_scheduler_publishes_closed_month_once_and_resumes_from_catalog(tmp_path):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    now = [datetime(2026, 11, 1, tzinfo=timezone.utc)]
    catalog, plan = retained_scorecard(
        tmp_path, evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    worker = EvolutionScheduler(catalog, lambda: now[0])

    first = worker.step()
    second = worker.step()
    restarted = EvolutionScheduler(
        EvolutionCatalog(tmp_path / "evaluation.sqlite3"), lambda: now[0]
    ).step()

    assert first["state"] == "WAITING"
    assert first["processed_this_cycle"] == 1
    assert first["pending_tournaments"] == 0
    assert second["processed_this_cycle"] == 0
    assert restarted["processed_this_cycle"] == 0
    assert catalog.monthly_tournament(plan.protocol_sha256, "2026-10")
    assert sum(row["kind"] == "TOURNAMENT" for row in catalog.inventory()) == 1


def test_scheduler_publishes_repeated_training_quality_profile_once(tmp_path):
    from datetime import timedelta

    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    now = [datetime(2026, 10, 20, tzinfo=timezone.utc)]
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    catalog = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    for evaluated_at in (now[0] - timedelta(days=1), now[0]):
        catalog.register(
            evaluate_protocol_outputs(
                plan,
                data,
                baseline,
                challenger,
                (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
                (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
                evaluated_at=evaluated_at,
            )
        )
    worker = EvolutionScheduler(catalog, lambda: now[0])

    first = worker.step()
    second = worker.step()

    assert first["processed_quality_profiles"] == 1
    assert first["pending_quality_profiles"] == 0
    assert second["processed_quality_profiles"] == 0
    original_refs = [row["ref"] for row in catalog.inventory() if row["kind"] == "QUALITY_PROFILE"]
    assert len(original_refs) == 1
    original = catalog.quality_profile(original_refs[0])
    assert original is not None and original.run_count == 2

    now[0] += timedelta(minutes=1)
    catalog.register(
        evaluate_protocol_outputs(
            plan,
            data,
            baseline,
            challenger,
            (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=now[0],
        )
    )
    updated = worker.step()
    profile_refs = [row["ref"] for row in catalog.inventory() if row["kind"] == "QUALITY_PROFILE"]
    assert updated["processed_quality_profiles"] == 1
    assert len(profile_refs) == 2
    assert original_refs[0] in profile_refs
    assert catalog.quality_profile(original_refs[0]).run_count == 2
    current = max(
        (catalog.quality_profile(ref) for ref in profile_refs),
        key=lambda row: row.latest_evaluated_at,
    )
    assert current.candidate_genome_ref == challenger.genome_ref
    assert current.run_count == 3


def test_parallel_workers_publish_one_monthly_tournament(tmp_path):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    catalog, _ = retained_scorecard(
        tmp_path, evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )

    def now():
        return datetime(2026, 11, 1, tzinfo=timezone.utc)

    workers = (
        EvolutionScheduler(catalog, now),
        EvolutionScheduler(EvolutionCatalog(catalog.path), now),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = tuple(pool.map(lambda worker: worker.step(), workers))

    assert all(status["state"] == "WAITING" for status in statuses)
    processed = tuple(status["processed_this_cycle"] for status in statuses)
    assert all(count in (0, 1) for count in processed)
    assert sum(processed) >= 1
    assert sum(row["kind"] == "TOURNAMENT" for row in catalog.inventory()) == 1


def test_scheduler_waits_for_month_close_and_retained_scorecards(tmp_path):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    catalog, plan = retained_scorecard(
        tmp_path, evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    worker = EvolutionScheduler(
        catalog, lambda: datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
    )
    assert worker.step()["pending_tournaments"] == 0
    assert all(row["kind"] != "TOURNAMENT" for row in catalog.inventory())

    empty_catalog = EvolutionCatalog(tmp_path / "empty" / "evaluation.sqlite3")
    empty_catalog.register(dataset())
    empty_catalog.register(plan)
    empty_worker = EvolutionScheduler(
        empty_catalog, lambda: datetime(2026, 11, 1, tzinfo=timezone.utc)
    )
    status = empty_worker.step()
    assert status["state"] == "WAITING"
    assert status["pending_tournaments"] == 0
    assert status["processed_this_cycle"] == 0


def test_scheduler_refuses_naive_or_regressing_clocks(tmp_path):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    catalog = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    now = [datetime(2026, 11, 1, tzinfo=timezone.utc)]
    worker = EvolutionScheduler(catalog, lambda: now[0])
    worker.step()
    now[0] = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="clock regressed"):
        worker.step()
    with pytest.raises(ValueError, match="include a timezone"):
        EvolutionScheduler(catalog, lambda: datetime(2026, 11, 1)).step()


def test_scheduler_reports_corrupt_catalog_without_creating_tournament(tmp_path):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler

    catalog, _ = retained_scorecard(
        tmp_path, evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    with catalog._connection() as db:
        db.execute("DELETE FROM audit_events")
    worker = EvolutionScheduler(catalog, lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
    status = worker.step()
    assert status["state"] == "ERROR"
    assert status["last_error"] == "EVOLUTION_CATALOG_UNAVAILABLE"
    assert status["promotion_allowed"] is False


def test_scheduler_claims_training_jobs_once_and_records_unverified_scorecard(
    tmp_path, monkeypatch
):
    from ai_quant_os.evolution_scheduler import EvolutionScheduler
    from ai_quant_os.evolution_jobs import EvolutionJobQueue
    from ai_quant_os.evolution_runner import EvolutionRunner, GenomeExecutionResult
    from test_evolution_jobs import retained_training_pair

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    queue = EvolutionJobQueue(catalog, genomes, clock=lambda: now)
    queued = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)

    def fake_run(self, genome, data, protocol, *, baseline=None):
        return GenomeExecutionResult(
            genome_ref=genome.genome_ref,
            dataset_ref=data.dataset_ref,
            outputs=({"case_id": data.cases[0].case_id, "output": {"regime": "TRENDING"}},),
        )

    monkeypatch.setattr(EvolutionRunner, "run", fake_run)
    worker = EvolutionScheduler(catalog, lambda: now, job_queue=queue)

    first = worker.step()
    second = worker.step()

    result = queue.list_jobs()[0]
    assert result.job_ref == queued.job_ref
    assert result.state == "COMPLETED"
    assert result.evaluation_verified is False
    assert result.promotion_allowed is False
    assert first["processed_evaluation_jobs"] == 1
    assert first["pending_evaluation_jobs"] == 0
    assert first["last_evaluation_job_state"] == "COMPLETED"
    assert second["processed_evaluation_jobs"] == 0


def test_application_scheduler_is_opt_in_operator_protected_and_runs_monthly_cycle(tmp_path):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    catalog, plan = retained_scorecard(
        tmp_path, evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    app = create_app(
        operator_token="operator",
        evolution_workspace=tmp_path,
        evolution_scheduler_enabled=True,
        clock=lambda: now,
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/evolution/scheduler").status_code == 401
        response = client.get(
            "/api/v1/evolution/scheduler", headers={"X-Operator-Token": "operator"}
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["enabled"] is True
        assert response.json()["evaluation_verified"] is False
        assert response.json()["promotion_allowed"] is False
        assert app.state.evolution_scheduler.first_cycle_completed.wait(timeout=5)
        response = client.get(
            "/api/v1/evolution/scheduler", headers={"X-Operator-Token": "operator"}
        )
        assert response.json()["processed_this_cycle"] == 1
        assert response.json()["pending_evaluation_jobs"] == 0
        assert response.json()["processed_evaluation_jobs"] == 0

    assert (
        catalog.tournament(
            catalog.monthly_tournament(plan.protocol_sha256, "2026-10").tournament_ref
        )
        is not None
    )


def test_application_scheduler_requires_opt_in_workspace_and_operator_auth(tmp_path):
    disabled = create_app(operator_token="operator")
    with TestClient(disabled) as client:
        assert (
            client.get(
                "/api/v1/evolution/scheduler", headers={"X-Operator-Token": "operator"}
            ).json()["state"]
            == "DISABLED"
        )

    with pytest.raises(ValueError, match="workspace and operator token"):
        create_app(evolution_scheduler_enabled=True, evolution_workspace=tmp_path)
    with pytest.raises(ValueError, match="workspace and operator token"):
        create_app(operator_token="operator", evolution_scheduler_enabled=True)
