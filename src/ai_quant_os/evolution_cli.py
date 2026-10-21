"""Archive agent genomes and run explicitly non-authoritative training checks."""

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ai_quant_os.agent_genomes import (
    AgentGenome,
    EvaluationObjective,
    MAX_GENOME_BYTES,
    encode_genome,
)
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_catalog import EvolutionCatalog
from ai_quant_os.evolution_jobs import EvolutionJobQueue
from ai_quant_os.evolution_runner import EvolutionRunner
from ai_quant_os.evolution_evaluation import (
    CaseOutput,
    EvaluationOutputBundle,
    MAX_EVALUATION_OUTPUTS_BYTES,
    encode_scorecard,
    encode_tournament,
    evaluate_protocol_outputs,
    scorecard_sha256,
)
from ai_quant_os.evolution_protocols import (
    EvolutionDataset,
    EvaluationProtocol,
    MAX_DATASET_BYTES,
    encode_protocol_artifact,
)
from ai_quant_os.genome_store import GenomeStore
from ai_quant_os.promotion_contracts import PromotionDecision
from ai_quant_os.promotion_store import PromotionEvidenceStore
from ai_quant_os.repair_image_inputs import read_repair_image_input


def _view(genome: AgentGenome) -> dict:
    return {
        "schema_version": "1.0",
        "genome_ref": genome.genome_ref,
        "agent_id": genome.manifest.agent_id,
        "agent_version": genome.manifest.agent_version,
        "manifest_ref": genome.manifest.manifest_ref,
        "parent_ref": genome.parent_ref,
        "state": genome.state,
        "objective": genome.objective.model_dump(mode="json"),
        "source_inventory": [{"path": row.path, "sha256": row.sha256} for row in genome.sources],
        "execution_verified": False,
        "promotion_allowed": False,
    }


def _export(output: Path, content: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        pending = Path(temporary.name)
    try:
        try:
            os.link(pending, output)
        except FileExistsError:
            if read_repair_image_input(output, MAX_GENOME_BYTES) != content:
                raise ValueError("Immutable artifact export differs") from None
    finally:
        pending.unlink(missing_ok=True)


def _training_inputs(catalog: EvolutionCatalog, root: Path, args):
    plan = catalog.protocol(args.protocol)
    if plan is None:
        raise ValueError("Unknown retained protocol")
    registry = load_agent_catalog()
    data = catalog.dataset(
        plan.dataset_ref,
        registry=registry,
        agent_id="A09",
        agent_version="0.2.0",
    )
    genomes = GenomeStore(root / "genomes.sqlite3", registry=registry)
    baseline = genomes.get(args.baseline_genome)
    challenger = genomes.get(args.challenger_genome)
    if baseline is None or challenger is None:
        raise ValueError("Unknown baseline or challenger genome")
    return plan, data, baseline, challenger


def _run_training_pair(plan, data, baseline, challenger):
    runner = EvolutionRunner()
    return (
        runner.run(baseline, data, plan),
        runner.run(challenger, data, plan, baseline=baseline),
    )


def _case_outputs(rows) -> tuple[CaseOutput, ...]:
    return tuple(
        CaseOutput(
            case_id=row["case_id"],
            output_json=json.dumps(row["output"], sort_keys=True, separators=(",", ":")),
        )
        for row in rows
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agent genomes, training diagnostics and lineage; no release authority"
    )
    parser.add_argument("--workspace", type=Path, default=Path("data-local/evolution"))
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("agent_id")
    objective_source = seed.add_mutually_exclusive_group(required=True)
    objective_source.add_argument("--objective", type=Path)
    objective_source.add_argument("--protocol")
    register = commands.add_parser("register")
    register.add_argument("--input", type=Path, required=True)
    commands.add_parser("list")
    lineage = commands.add_parser("lineage")
    lineage.add_argument("genome_ref")
    export = commands.add_parser("export")
    export.add_argument("genome_ref")
    export.add_argument("--output", type=Path, required=True)
    for command in ("dataset-register", "protocol-register"):
        commands.add_parser(command).add_argument("--input", type=Path, required=True)
    execute = commands.add_parser("execute-training")
    execute.add_argument("--protocol", required=True)
    execute.add_argument("--baseline-genome", required=True)
    execute.add_argument("--challenger-genome", required=True)
    execute.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate-training")
    evaluate.add_argument("--protocol", required=True)
    evaluate.add_argument("--baseline-genome", required=True)
    evaluate.add_argument("--challenger-genome", required=True)
    evaluate.add_argument("--evaluated-at", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    commands.add_parser("catalog-list")
    protocol_export = commands.add_parser("protocol-export")
    protocol_export.add_argument("protocol_sha256")
    protocol_export.add_argument("--output", type=Path, required=True)
    measure = commands.add_parser("measure")
    measure.add_argument("--protocol", required=True)
    measure.add_argument("--baseline-genome", required=True)
    measure.add_argument("--challenger-genome", required=True)
    measure.add_argument("--outputs", type=Path, required=True)
    measure.add_argument("--evaluated-at", required=True)
    measure.add_argument("--output", type=Path, required=True)
    tournament = commands.add_parser("tournament")
    tournament.add_argument("--protocol", required=True)
    tournament.add_argument("--period", required=True)
    tournament.add_argument("--output", type=Path, required=True)
    queue_training = commands.add_parser("queue-training")
    queue_training.add_argument("--protocol", required=True)
    queue_training.add_argument("--baseline-genome", required=True)
    queue_training.add_argument("--challenger-genome", required=True)
    commands.add_parser("list-training-jobs")
    recover_training = commands.add_parser("recover-training")
    recover_training.add_argument("--job-ref", required=True)
    recover_training.add_argument("--expected-updated-at", required=True)
    recover_training.add_argument("--recovery-id", required=True)
    recover_training.add_argument("--reason", required=True)
    recover_training.add_argument("--confirm-worker-stopped", action="store_true", required=True)
    quality_profile = commands.add_parser("quality-profile")
    quality_profile.add_argument("--protocol", required=True)
    quality_profile.add_argument("--challenger-genome", required=True)
    promotion_record = commands.add_parser("promotion-record")
    promotion_record.add_argument("--input", type=Path, required=True)
    promotion_get = commands.add_parser("promotion-get")
    promotion_get.add_argument("promotion_decision_ref")
    commands.add_parser("promotion-list")
    args = parser.parse_args()
    try:
        root = args.workspace.resolve()
        if args.command in {"promotion-record", "promotion-get", "promotion-list"}:
            if args.command == "promotion-record":
                decision = PromotionDecision.model_validate_json(
                    read_repair_image_input(args.input, 1048576)
                )
                store = PromotionEvidenceStore(root / "promotion.sqlite3")
                ref = store.register(decision)
                result = {"promotion_decision_ref": ref, **decision.model_dump(mode="json")}
            elif args.command == "promotion-get":
                store = PromotionEvidenceStore(root / "promotion.sqlite3")
                result = store.get(args.promotion_decision_ref).model_dump(mode="json")
            else:
                store = PromotionEvidenceStore(root / "promotion.sqlite3")
                result = {"schema_version": "1.0", "refs": list(store.list_refs())}
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "seed" and args.objective is not None:
            objective = EvaluationObjective.model_validate_json(
                read_repair_image_input(args.objective, 8192)
            )
        elif args.command == "register":
            genome = AgentGenome.model_validate_json(
                read_repair_image_input(args.input, MAX_GENOME_BYTES)
            )
        elif args.command in {"dataset-register", "protocol-register"}:
            model, limit = (
                (EvolutionDataset, MAX_DATASET_BYTES)
                if args.command == "dataset-register"
                else (EvaluationProtocol, 8192)
            )
            artifact = model.model_validate_json(read_repair_image_input(args.input, limit))
        elif args.command in {
            "measure",
            "tournament",
            "execute-training",
            "evaluate-training",
        }:
            output = args.output.resolve()
            if output.is_relative_to(root) or output.suffix.lower() != ".json":
                raise ValueError(
                    "Tournament/scorecard export requires JSON outside registry storage"
                )
        if args.command == "measure":
            bundle = EvaluationOutputBundle.model_validate_json(
                read_repair_image_input(args.outputs, MAX_EVALUATION_OUTPUTS_BYTES)
            )
        elif args.command in {"export", "protocol-export"}:
            output = args.output.resolve()
            if output.is_relative_to(root) or output.suffix.lower() != ".json":
                raise ValueError("Export requires a JSON path outside registry storage")
        if args.command in {
            "dataset-register",
            "protocol-register",
            "catalog-list",
            "protocol-export",
            "measure",
            "tournament",
            "execute-training",
            "evaluate-training",
            "queue-training",
            "list-training-jobs",
            "recover-training",
            "quality-profile",
        }:
            catalog = EvolutionCatalog(root / "evaluation.sqlite3")
            if args.command in {"queue-training", "list-training-jobs", "recover-training"}:
                registry = load_agent_catalog()
                genomes = GenomeStore(root / "genomes.sqlite3", registry=registry)
                queue = EvolutionJobQueue(
                    catalog,
                    genomes,
                    clock=lambda: datetime.now(timezone.utc),
                )
                if args.command == "queue-training":
                    job = queue.enqueue(args.protocol, args.baseline_genome, args.challenger_genome)
                    result = job.model_dump(mode="json")
                elif args.command == "list-training-jobs":
                    result = {
                        "schema_version": "1.0",
                        "jobs": [job.model_dump(mode="json") for job in queue.list_jobs()],
                    }
                else:
                    expected_updated_at = datetime.fromisoformat(args.expected_updated_at)
                    if (
                        expected_updated_at.tzinfo is None
                        or expected_updated_at.utcoffset() is None
                    ):
                        raise ValueError("Expected job timestamp must include a timezone")
                    job = queue.recover_running(
                        args.job_ref,
                        expected_updated_at=expected_updated_at,
                        recovery_id=args.recovery_id,
                        reason=args.reason,
                        worker_stopped=args.confirm_worker_stopped,
                    )
                    result = job.model_dump(mode="json")
            elif args.command == "quality-profile":
                profile = catalog.training_quality_profile(args.protocol, args.challenger_genome)
                retained = catalog.register(profile)
                result = {
                    "quality_profile_ref": retained.quality_profile_ref,
                    **retained.model_dump(mode="json"),
                }
            elif args.command == "measure":
                evaluated_at = datetime.fromisoformat(args.evaluated_at)
                if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
                    raise ValueError("Evaluation timestamp must include a timezone")
                plan = catalog.protocol(args.protocol)
                if plan is None:
                    raise ValueError("Unknown retained protocol")
                registry = load_agent_catalog()
                data = catalog.dataset(
                    plan.dataset_ref,
                    registry=registry,
                    agent_id="A09",
                    agent_version="0.2.0",
                )
                genomes = GenomeStore(root / "genomes.sqlite3", registry=registry)
                baseline = genomes.get(args.baseline_genome)
                challenger = genomes.get(args.challenger_genome)
                if baseline is None or challenger is None:
                    raise ValueError("Unknown baseline or challenger genome")
                report = evaluate_protocol_outputs(
                    plan,
                    data,
                    baseline,
                    challenger,
                    bundle.baseline_outputs,
                    bundle.challenger_outputs,
                    evaluated_at=evaluated_at,
                )
                retained = catalog.register(report)
                _export(output, encode_scorecard(retained))
                result = {
                    "diagnostic": retained.diagnostic,
                    "scorecard_ref": retained.scorecard_ref,
                    "scorecard_sha256": scorecard_sha256(retained),
                    "evaluation_verified": False,
                    "promotion_allowed": False,
                }
            elif args.command == "execute-training":
                plan, data, baseline, challenger = _training_inputs(catalog, root, args)
                baseline_result, challenger_result = _run_training_pair(
                    plan, data, baseline, challenger
                )
                bundle = EvaluationOutputBundle(
                    schema_version="1.0",
                    baseline_outputs=_case_outputs(baseline_result.outputs),
                    challenger_outputs=_case_outputs(challenger_result.outputs),
                )
                _export(
                    output,
                    json.dumps(
                        bundle.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode(),
                )
                result = {
                    "baseline_genome_ref": baseline.genome_ref,
                    "challenger_genome_ref": challenger.genome_ref,
                    "dataset_ref": data.dataset_ref,
                    "protocol_sha256": plan.protocol_sha256,
                    "case_count": len(data.cases),
                    "output_bundle": str(output),
                    "execution_verified": False,
                    "promotion_allowed": False,
                }
            elif args.command == "evaluate-training":
                evaluated_at = datetime.fromisoformat(args.evaluated_at)
                if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
                    raise ValueError("Evaluation timestamp must include a timezone")
                plan, data, baseline, challenger = _training_inputs(catalog, root, args)
                baseline_result, challenger_result = _run_training_pair(
                    plan, data, baseline, challenger
                )
                report = evaluate_protocol_outputs(
                    plan,
                    data,
                    baseline,
                    challenger,
                    _case_outputs(baseline_result.outputs),
                    _case_outputs(challenger_result.outputs),
                    evaluated_at=evaluated_at,
                )
                retained = catalog.register(report)
                _export(output, encode_scorecard(retained))
                result = {
                    "diagnostic": retained.diagnostic,
                    "scorecard_ref": retained.scorecard_ref,
                    "scorecard_sha256": scorecard_sha256(retained),
                    "evaluation_verified": False,
                    "promotion_allowed": False,
                }
            elif args.command == "tournament":
                report = catalog.monthly_tournament(args.protocol, args.period)
                retained = catalog.register(report)
                _export(output, encode_tournament(retained))
                result = {
                    "tournament_ref": retained.tournament_ref,
                    "period": retained.period,
                    "candidate_count": len(retained.rankings),
                    "winner_genome_ref": retained.rankings[0].challenger_genome_ref,
                    "evaluation_verified": False,
                    "promotion_allowed": False,
                }
            elif args.command in {"dataset-register", "protocol-register"}:
                artifact = catalog.register(artifact)
                ref = (
                    artifact.dataset_ref
                    if type(artifact) is EvolutionDataset
                    else artifact.protocol_sha256
                )
                result = next(row for row in catalog.inventory() if row["ref"] == ref)
            elif args.command == "catalog-list":
                result = {"schema_version": "1.0", "artifacts": catalog.inventory()}
            else:
                plan = catalog.protocol(args.protocol_sha256)
                if plan is None:
                    raise ValueError("Unknown retained protocol")
                _export(output, encode_protocol_artifact(plan))
                result = {"protocol_sha256": plan.protocol_sha256}
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "seed" and args.protocol is not None:
            plan = EvolutionCatalog(root / "evaluation.sqlite3").protocol(args.protocol)
            if plan is None or plan.agent_id != args.agent_id:
                raise ValueError("Retained same-agent protocol required")
            objective = plan.objective
        store = GenomeStore(root / "genomes.sqlite3", registry=load_agent_catalog())
        if args.command == "seed":
            result = _view(store.seed(args.agent_id, objective))
        elif args.command == "register":
            result = _view(store.register(genome))
        elif args.command == "list":
            result = {"schema_version": "1.0", "genomes": [_view(row) for row in store.list()]}
        elif args.command == "lineage":
            result = {
                "schema_version": "1.0",
                "lineage": [_view(row) for row in store.lineage(args.genome_ref)],
            }
        else:
            genome = store.get(args.genome_ref)
            if genome is None:
                raise ValueError("Unknown genome reference")
            _export(output, encode_genome(genome))
            result = _view(genome)
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        parser.exit(
            2,
            "Evolution operation refused; check input, lineage, storage integrity and output path.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
