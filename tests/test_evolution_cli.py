"""Genome archives, retained training execution and diagnostic CLI coverage."""

import json
import os
import subprocess
import sys

import pytest

from test_agent_genomes import baseline, child, objective


def run(workspace, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.evolution_cli",
            "--workspace",
            str(workspace),
            *map(str, arguments),
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        timeout=15,
    )


def test_seed_register_restart_lineage_and_immutable_export(tmp_path):
    workspace = tmp_path / "registry"
    objective_path = tmp_path / "objective.json"
    objective_path.write_text(objective().model_dump_json(), encoding="utf-8")
    result = run(workspace, "seed", "A02", "--objective", objective_path)
    assert result.returncode == 0, result.stderr.decode()
    root = baseline()
    assert json.loads(result.stdout)["genome_ref"] == root.genome_ref
    candidate = child(root)
    input_path = tmp_path / "candidate.json"
    input_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    result = run(workspace, "register", "--input", input_path)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["state"] == "UNVERIFIED_CHALLENGER"
    assert json.loads(run(workspace, "list").stdout)["genomes"][0]["promotion_allowed"] is False
    lineage = json.loads(run(workspace, "lineage", candidate.genome_ref).stdout)["lineage"]
    assert [row["genome_ref"] for row in lineage] == [root.genome_ref, candidate.genome_ref]
    output = tmp_path / "export.json"
    result = run(workspace, "export", candidate.genome_ref, "--output", output)
    assert result.returncode == 0, result.stderr.decode()
    original = output.read_bytes()
    assert json.loads(original) == candidate.model_dump(mode="json")
    assert run(workspace, "export", candidate.genome_ref, "--output", output).returncode == 0
    assert output.read_bytes() == original
    output.write_bytes(b"keep this file")
    assert run(workspace, "export", candidate.genome_ref, "--output", output).returncode == 2
    assert output.read_bytes() == b"keep this file"


def test_promotion_evidence_cli_records_reads_and_lists_fail_closed_snapshots(tmp_path):
    from datetime import datetime, timezone

    from ai_quant_os.promotion_contracts import PromotionDecision

    workspace = tmp_path / "promotion-workspace"
    source = tmp_path / "promotion-decision.json"
    evidence = PromotionDecision(
        candidate_id="candidate-1",
        current_champion_id="champion-1",
        offline_eval={"status": "NOT_RUN", "reason": "OFFLINE_GATE_NOT_CONNECTED"},
        replay_eval={"status": "NOT_RUN", "reason": "REPLAY_GATE_NOT_CONNECTED"},
        adversarial_eval={"status": "NOT_RUN", "reason": "ADVERSARIAL_GATE_NOT_CONNECTED"},
        shadow_eval={"status": "NOT_RUN", "reason": "SHADOW_GATE_NOT_CONNECTED"},
        canary_eval={"status": "NOT_RUN", "reason": "CANARY_GATE_NOT_CONNECTED"},
        rollback_target="champion-1",
        created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )
    source.write_text(evidence.model_dump_json(), encoding="utf-8")

    recorded = run(workspace, "promotion-record", "--input", source)
    assert recorded.returncode == 0, recorded.stderr.decode()
    record_summary = json.loads(recorded.stdout)
    ref = record_summary["promotion_decision_ref"]
    assert record_summary["decision"] == "WAIT_FOR_EVIDENCE"
    assert record_summary["approved_by_policy"] is False
    assert run(workspace, "promotion-record", "--input", source).stdout == recorded.stdout

    fetched = run(workspace, "promotion-get", ref)
    assert fetched.returncode == 0, fetched.stderr.decode()
    assert json.loads(fetched.stdout) == evidence.model_dump(mode="json")
    listed = run(workspace, "promotion-list")
    assert listed.returncode == 0, listed.stderr.decode()
    assert json.loads(listed.stdout)["refs"] == [ref]


def test_promotion_evidence_cli_rejects_attempted_approval(tmp_path):
    from datetime import datetime, timezone

    from ai_quant_os.promotion_contracts import PromotionDecision

    payload = PromotionDecision(
        candidate_id="candidate-1",
        current_champion_id=None,
        offline_eval={"status": "NOT_RUN", "reason": "NOT_CONNECTED"},
        replay_eval={"status": "NOT_RUN", "reason": "NOT_CONNECTED"},
        adversarial_eval={"status": "NOT_RUN", "reason": "NOT_CONNECTED"},
        shadow_eval={"status": "NOT_RUN", "reason": "NOT_CONNECTED"},
        canary_eval={"status": "NOT_RUN", "reason": "NOT_CONNECTED"},
        created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
    ).model_dump(mode="json")
    payload["approved_by_policy"] = True
    source = tmp_path / "approved.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = run(tmp_path / "workspace", "promotion-record", "--input", source)

    assert result.returncode == 2
    assert result.stdout == b""
    assert not (tmp_path / "workspace").exists()


def test_execute_training_cli_writes_bounded_baseline_and_challenger_bundle(
    tmp_path, monkeypatch, capsys
):
    from ai_quant_os.agent_genomes import capture_baseline_genome
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.evolution_catalog import EvolutionCatalog
    from ai_quant_os.evolution_cli import main
    from ai_quant_os.evolution_evaluation import EvaluationOutputBundle
    from ai_quant_os.evolution_runner import EvolutionRunner, GenomeExecutionResult
    from test_evolution_evaluation import protocol
    from test_evolution_protocols import dataset

    workspace = tmp_path / "workspace"
    output = tmp_path / "training-outputs.json"
    report_output = tmp_path / "training-scorecard.json"
    data = dataset()
    plan = protocol(data)
    registry = load_agent_catalog()
    baseline = capture_baseline_genome(registry, "A02", plan.objective)
    candidate = child(baseline)
    catalog = EvolutionCatalog(workspace / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    from ai_quant_os.genome_store import GenomeStore

    genomes = GenomeStore(workspace / "genomes.sqlite3", registry=registry)
    genomes.register(baseline)
    genomes.register(candidate)

    def fake_run(self, genome, dataset_value, protocol_value, *, baseline=None):
        payload = {"regime": "RANGING" if genome.parent_ref is None else "TRENDING"}
        return GenomeExecutionResult(
            genome_ref=genome.genome_ref,
            dataset_ref=dataset_value.dataset_ref,
            outputs=({"case_id": dataset_value.cases[0].case_id, "output": payload},),
        )

    monkeypatch.setattr(EvolutionRunner, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-evolve",
            "--workspace",
            str(workspace),
            "execute-training",
            "--protocol",
            plan.protocol_sha256,
            "--baseline-genome",
            baseline.genome_ref,
            "--challenger-genome",
            candidate.genome_ref,
            "--output",
            str(output),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution_verified"] is False
    assert result["promotion_allowed"] is False
    bundle = EvaluationOutputBundle.model_validate_json(output.read_bytes())
    assert json.loads(bundle.baseline_outputs[0].output_json) == {"regime": "RANGING"}
    assert json.loads(bundle.challenger_outputs[0].output_json) == {"regime": "TRENDING"}

    monkeypatch.setattr(
        "sys.argv",
        [
            "ai-quant-evolve",
            "--workspace",
            str(workspace),
            "evaluate-training",
            "--protocol",
            plan.protocol_sha256,
            "--baseline-genome",
            baseline.genome_ref,
            "--challenger-genome",
            candidate.genome_ref,
            "--evaluated-at",
            "2026-10-17T00:00:00+00:00",
            "--output",
            str(report_output),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostic"] == "IMPROVES_ON_TRAINING_DATA"
    assert result["evaluation_verified"] is False
    assert result["promotion_allowed"] is False
    assert json.loads(report_output.read_bytes())["challenger_exact_matches"] == 1


def test_invalid_input_refuses_before_creating_registry(tmp_path):
    workspace = tmp_path / "registry"
    source = tmp_path / "bad.json"
    source.write_bytes(b"{}")
    result = run(workspace, "register", "--input", source)
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Traceback" not in result.stderr
    assert not workspace.exists()


def test_export_refuses_registry_storage_and_non_json_paths(tmp_path):
    workspace = tmp_path / "registry"
    for output in (
        workspace / "export.json",
        tmp_path / "candidate.py",
        tmp_path / "state.sqlite3",
    ):
        result = run(workspace, "export", baseline().genome_ref, "--output", output)
        assert result.returncode == 2
        assert not workspace.exists()


def test_planned_agent_seed_and_oversize_genome_refuse_without_success(tmp_path):
    workspace = tmp_path / "registry"
    metadata = tmp_path / "objective.json"
    metadata.write_text(objective().model_dump_json(), encoding="utf-8")
    result = run(workspace, "seed", "A09", "--objective", metadata)
    assert result.returncode == 2
    assert result.stdout == b""
    oversized = tmp_path / "oversize.json"
    oversized.write_bytes(b" " * 262145)
    result = run(workspace, "register", "--input", oversized)
    assert result.returncode == 2
    assert result.stdout == b""


def test_retained_protocol_can_seed_exact_objective_and_export_after_restart(tmp_path):
    from test_evolution_protocols import dataset, protocol

    workspace = tmp_path / "registry"
    data = dataset()
    plan = protocol(data)
    source = tmp_path / "artifact.json"
    source.write_text(data.model_dump_json(), encoding="utf-8")
    result = run(workspace, "dataset-register", "--input", source)
    assert result.returncode == 0, result.stderr.decode()
    assert "cases" not in json.loads(result.stdout)
    source.write_text(plan.model_dump_json(), encoding="utf-8")
    result = run(workspace, "protocol-register", "--input", source)
    assert result.returncode == 0, result.stderr.decode()
    result = run(workspace, "seed", "A02", "--protocol", plan.protocol_sha256)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["objective"]["protocol_sha256"] == plan.protocol_sha256
    assert len(json.loads(run(workspace, "catalog-list").stdout)["artifacts"]) == 2
    output = tmp_path / "protocol.json"
    assert (
        run(workspace, "protocol-export", plan.protocol_sha256, "--output", output).returncode == 0
    )
    assert json.loads(output.read_bytes()) == plan.model_dump(mode="json")
    original = output.read_bytes()
    assert (
        run(workspace, "protocol-export", plan.protocol_sha256, "--output", output).returncode == 0
    )
    assert output.read_bytes() == original
    assert run(workspace, "seed", "A07", "--protocol", plan.protocol_sha256).returncode == 2
    output.write_bytes(b"preserve prior protocol file")
    result = run(workspace, "protocol-export", plan.protocol_sha256, "--output", output)
    assert result.returncode == 2
    assert result.stdout == b""
    assert output.read_bytes() == b"preserve prior protocol file"


@pytest.mark.parametrize("command", ["dataset-register", "protocol-register"])
def test_invalid_evaluation_input_refuses_before_creating_storage(tmp_path, command):
    workspace = tmp_path / "registry"
    source = tmp_path / "bad.json"
    source.write_bytes(b"{}")
    result = run(workspace, command, "--input", source)
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Traceback" not in result.stderr
    assert not workspace.exists()


def test_protocol_export_refuses_non_json_and_catalog_paths_before_storage(tmp_path):
    workspace = tmp_path / "registry"
    for output in (workspace / "protocol.json", tmp_path / "protocol.py"):
        result = run(workspace, "protocol-export", "a" * 64, "--output", output)
        assert result.returncode == 2
        assert result.stdout == b""
        assert not workspace.exists()


def test_measure_uses_retained_inputs_and_immutably_exports_diagnostic(tmp_path):
    from ai_quant_os.agent_genomes import capture_baseline_genome
    from ai_quant_os.agent_registry import load_agent_catalog
    from test_evolution_protocols import dataset, protocol

    workspace = tmp_path / "registry"
    data = dataset()
    plan = protocol(data)
    artifact = tmp_path / "artifact.json"
    artifact.write_text(data.model_dump_json(), encoding="utf-8")
    assert run(workspace, "dataset-register", "--input", artifact).returncode == 0
    artifact.write_text(plan.model_dump_json(), encoding="utf-8")
    assert run(workspace, "protocol-register", "--input", artifact).returncode == 0
    root_result = run(workspace, "seed", "A02", "--protocol", plan.protocol_sha256)
    assert root_result.returncode == 0
    root = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    candidate = child(root)
    artifact.write_text(candidate.model_dump_json(), encoding="utf-8")
    assert run(workspace, "register", "--input", artifact).returncode == 0

    outputs = tmp_path / "outputs.json"
    outputs.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "baseline_outputs": [{"case_id": "case-1", "output_json": '{"regime":"OTHER"}'}],
                "challenger_outputs": [
                    {"case_id": "case-1", "output_json": '{"regime":"TRENDING"}'}
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "scorecard.json"
    evaluated_at = "2026-10-14T00:00:00+00:00"
    result = run(
        workspace,
        "measure",
        "--protocol",
        plan.protocol_sha256,
        "--baseline-genome",
        root.genome_ref,
        "--challenger-genome",
        candidate.genome_ref,
        "--outputs",
        outputs,
        "--evaluated-at",
        evaluated_at,
        "--output",
        report_path,
    )

    assert result.returncode == 0, result.stderr.decode()
    summary = json.loads(result.stdout)
    report = json.loads(report_path.read_bytes())
    assert summary["diagnostic"] == "IMPROVES_ON_TRAINING_DATA"
    assert summary["scorecard_ref"] == f"evolution-scorecard:{summary['scorecard_sha256']}"
    assert report["challenger_score"] == "1"
    assert report["evaluation_verified"] is False
    assert report["promotion_allowed"] is False
    catalog_rows = json.loads(run(workspace, "catalog-list").stdout)["artifacts"]
    assert sum(row["kind"] == "SCORECARD" for row in catalog_rows) == 1
    saved = report_path.read_bytes()
    assert (
        run(
            workspace,
            "measure",
            "--protocol",
            plan.protocol_sha256,
            "--baseline-genome",
            root.genome_ref,
            "--challenger-genome",
            candidate.genome_ref,
            "--outputs",
            outputs,
            "--evaluated-at",
            evaluated_at,
            "--output",
            report_path,
        ).returncode
        == 0
    )
    assert report_path.read_bytes() == saved

    tournament_path = tmp_path / "tournament.json"
    result = run(
        workspace,
        "tournament",
        "--protocol",
        plan.protocol_sha256,
        "--period",
        "2026-10",
        "--output",
        tournament_path,
    )
    assert result.returncode == 0, result.stderr.decode()
    tournament_summary = json.loads(result.stdout)
    tournament_report = json.loads(tournament_path.read_bytes())
    assert tournament_summary["candidate_count"] == 1
    assert tournament_summary["winner_genome_ref"] == candidate.genome_ref
    assert tournament_report["evaluation_verified"] is False
    assert tournament_report["promotion_allowed"] is False
    assert json.loads(run(workspace, "catalog-list").stdout)["artifacts"][-1]["kind"] == (
        "TOURNAMENT"
    )


def test_queue_training_cli_persists_one_idempotent_non_authoritative_job(tmp_path):
    from ai_quant_os.agent_genomes import capture_baseline_genome
    from ai_quant_os.agent_registry import load_agent_catalog
    from test_evolution_protocols import dataset, protocol

    workspace = tmp_path / "registry"
    data = dataset()
    plan = protocol(data)
    artifact = tmp_path / "artifact.json"
    artifact.write_text(data.model_dump_json(), encoding="utf-8")
    assert run(workspace, "dataset-register", "--input", artifact).returncode == 0
    artifact.write_text(plan.model_dump_json(), encoding="utf-8")
    assert run(workspace, "protocol-register", "--input", artifact).returncode == 0
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    assert run(workspace, "seed", "A02", "--protocol", plan.protocol_sha256).returncode == 0
    challenger = child(baseline)
    artifact.write_text(challenger.model_dump_json(), encoding="utf-8")
    assert run(workspace, "register", "--input", artifact).returncode == 0

    arguments = (
        "queue-training",
        "--protocol",
        plan.protocol_sha256,
        "--baseline-genome",
        baseline.genome_ref,
        "--challenger-genome",
        challenger.genome_ref,
    )
    first = run(workspace, *arguments)
    second = run(workspace, *arguments)

    assert first.returncode == 0, first.stderr.decode()
    assert second.returncode == 0, second.stderr.decode()
    first_job = json.loads(first.stdout)
    second_job = json.loads(second.stdout)
    assert first_job["job_ref"] == second_job["job_ref"]
    assert first_job["state"] == "PENDING"
    assert first_job["evaluation_verified"] is False
    assert first_job["promotion_allowed"] is False


def test_recover_training_cli_requires_explicit_stopped_worker_confirmation(tmp_path):
    from datetime import datetime, timezone
    from ai_quant_os.evolution_jobs import EvolutionJobQueue
    from test_evolution_jobs import retained_training_pair

    catalog, genomes, plan, baseline, challenger = retained_training_pair(tmp_path)
    now = datetime.now(timezone.utc)
    queue = EvolutionJobQueue(catalog, genomes, clock=lambda: now)
    queued = queue.enqueue(plan.protocol_sha256, baseline.genome_ref, challenger.genome_ref)
    running = queue.claim_next()
    arguments = (
        "recover-training",
        "--job-ref",
        queued.job_ref,
        "--expected-updated-at",
        running.updated_at.isoformat(),
        "--recovery-id",
        "operator-recovery-1",
        "--reason",
        "Verified original scheduler process stopped",
    )

    refused = run(tmp_path, *arguments)
    assert refused.returncode == 2
    assert refused.stdout == b""

    recovered = run(tmp_path, *arguments, "--confirm-worker-stopped")
    listed = run(tmp_path, "list-training-jobs")
    assert recovered.returncode == 0, recovered.stderr.decode()
    assert listed.returncode == 0, listed.stderr.decode()
    assert json.loads(recovered.stdout)["state"] == "PENDING"
    jobs = json.loads(listed.stdout)["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_ref"] == queued.job_ref
    assert jobs[0]["recovery_id"] == "operator-recovery-1"
