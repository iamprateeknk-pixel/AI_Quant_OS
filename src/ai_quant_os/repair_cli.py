"""Local, proposal-only repair candidate commands; never executes replacement code."""

import argparse
import json
import subprocess
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_store import HealthStore
from ai_quant_os.repair_workspace import RepairWorkspace
from ai_quant_os.repair_sandbox import DockerConfinement
from ai_quant_os.repair_replay import load_incident_replay
from ai_quant_os.repair_jobs import (
    prepared_repair_job,
    load_retained_repair_job,
    cleanup_staged_repair_job,
)
from ai_quant_os.repair_containers import (
    prepared_repair_container,
    cleanup_retained_repair_container,
    run_repair_verifier,
    read_repair_execution,
)


class RepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    incident_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    new_version: str = Field(max_length=32)
    replacements: dict[str, str] = Field(min_length=1, max_length=2)
    diagnosis: str = Field(min_length=1, max_length=4096)
    regression_plan: list[str] = Field(min_length=1, max_length=32)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and inspect isolated repair proposals")
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--health-store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "prepare", help="Save a candidate branch; verification stays pending"
    )
    create.add_argument("plan", type=Path)
    read = commands.add_parser(
        "inspect", help="Verify retained candidate source and receipt identity"
    )
    read.add_argument("candidate_id")
    snapshot = commands.add_parser(
        "snapshot", help="Inspect complete committed source inventory; execute nothing"
    )
    snapshot.add_argument("candidate_id")
    replay = commands.add_parser(
        "replay", help="Inspect verified archival incident inputs; execute nothing"
    )
    replay.add_argument("candidate_id")
    replay.add_argument("--graph-store", required=True, type=Path)
    admit = commands.add_parser(
        "admit", help="Create, inspect and remove an owned container; never start it"
    )
    admit.add_argument("candidate_id")
    admit.add_argument("--image-id", required=True)
    admit.add_argument("--suite-sha256", required=True)
    admit.add_argument("--suite-file", required=True, type=Path)
    admit.add_argument("--graph-store", type=Path)
    execute = commands.add_parser(
        "execute",
        help="Run the pinned verifier once in the owned Linux sandbox; result stays unattested",
    )
    execute.add_argument("candidate_id")
    execute.add_argument("--image-id", required=True)
    execute.add_argument("--suite-sha256", required=True)
    execute.add_argument("--suite-file", required=True, type=Path)
    execute.add_argument("--graph-store", type=Path)
    cleanup = commands.add_parser(
        "cleanup", help="Reconcile a retained job's exact owned container after restart"
    )
    cleanup.add_argument("candidate_id")
    cleanup.add_argument("--lease", required=True, type=Path)
    cleanup.add_argument("--image-id", required=True)
    cleanup.add_argument("--suite-sha256", required=True)
    cleanup.add_argument("--suite-file", required=True, type=Path)
    cleanup.add_argument("--graph-store", type=Path)
    staging = commands.add_parser(
        "cleanup-staging", help="Verify and remove orphan inputs without unresolved containers"
    )
    staging.add_argument("candidate_id")
    staging.add_argument("--stage", required=True, type=Path)
    staging.add_argument("--suite-sha256", required=True)
    staging.add_argument("--suite-file", required=True, type=Path)
    staging.add_argument("--graph-store", type=Path)
    sandbox = commands.add_parser(
        "sandbox-status", help="Read local sandbox prerequisites; execute nothing"
    )
    sandbox.add_argument("--image-id", required=True)
    sandbox.add_argument("--suite-sha256", required=True)
    execution_status = commands.add_parser(
        "execution-status", help="Read retained verifier evidence without contacting Docker"
    )
    execution_status.add_argument("owner")
    args = parser.parse_args(argv)
    try:
        if args.command == "sandbox-status":
            probe = DockerConfinement(args.image_id, args.suite_sha256).probe()
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        **asdict(probe),
                        "container_admission_required": True,
                        "suite_verification_required": True,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "execution-status":
            root = (
                args.root if args.root is not None else args.repository / "data-local" / "repairs"
            )
            report = read_repair_execution(root / "jobs", args.owner)
            print(json.dumps(asdict(report), sort_keys=True))
            return 0
        plan = None
        if args.command == "prepare":
            with args.plan.open("rb") as source:
                raw = source.read(3145729)
            if len(raw) > 3145728:
                raise ValueError("Repair plan exceeds bound")
            plan = RepairPlan.model_validate_json(raw)
        if args.health_store is None or not args.health_store.is_file():
            raise ValueError("An existing verified health journal is required")
        health = HealthStore(args.health_store)
        registry = load_agent_catalog()
        for incident in health.incidents():
            if incident.quarantine:
                registry.quarantine(
                    incident.agent_id, incident.agent_version, incident.manifest_ref
                )
        root = args.root if args.root is not None else args.repository / "data-local" / "repairs"
        workspace = RepairWorkspace(args.repository, root, health, registry)
        if args.command in {"admit", "execute", "cleanup", "cleanup-staging"}:
            policy = (
                None
                if args.command == "cleanup-staging"
                else DockerConfinement(args.image_id, args.suite_sha256)
            )
            source = workspace.snapshot(args.candidate_id)
            from ai_quant_os.repair_image_inputs import read_repair_image_input

            program = read_repair_image_input(args.suite_file, 65536)
            replay = (
                None if args.graph_store is None else load_incident_replay(source, args.graph_store)
            )
            if args.command == "cleanup-staging":
                receipt = cleanup_staged_repair_job(
                    source,
                    program,
                    suite_sha256=args.suite_sha256,
                    root=root / "jobs",
                    stage=args.stage,
                    replay=replay,
                )
                receipt["candidate_id"] = source.candidate_id
            elif args.command == "cleanup":
                job = load_retained_repair_job(
                    source,
                    program,
                    suite_sha256=args.suite_sha256,
                    image_id=args.image_id,
                    root=root / "jobs",
                    lease=args.lease,
                    replay=replay,
                )
                receipt = cleanup_retained_repair_container(policy, job)
                receipt["candidate_id"] = source.candidate_id
            elif args.command == "execute":
                with prepared_repair_job(
                    source,
                    program,
                    suite_sha256=args.suite_sha256,
                    root=root / "jobs",
                    replay=replay,
                ) as job:
                    with prepared_repair_container(policy, job) as created:
                        execution = run_repair_verifier(policy, job, created)
                        receipt = {
                            "evidence_root": str((root / "jobs").resolve()),
                            "candidate_id": source.candidate_id,
                            **asdict(execution),
                            "stdout_artifact": f"repair-execution-{created.owner}.stdout",
                            "stderr_artifact": f"repair-execution-{created.owner}.stderr",
                        }
            else:
                with prepared_repair_job(
                    source,
                    program,
                    suite_sha256=args.suite_sha256,
                    root=root / "jobs",
                    replay=replay,
                ) as job:
                    with prepared_repair_container(policy, job) as created:
                        receipt = {
                            "schema_version": "1.0",
                            "candidate_id": source.candidate_id,
                            "snapshot_sha256": source.snapshot_sha256,
                            "container_id": created.container_id,
                            "owner": created.owner,
                            "image_id": policy.image_id,
                            "suite_sha256": policy.suite_sha256,
                            "state": "REMOVED_AFTER_ADMISSION",
                            "execution_verified": False,
                            "promotion_allowed": False,
                        }
        elif args.command == "replay":
            source = workspace.snapshot(args.candidate_id)
            replay = load_incident_replay(source, args.graph_store)
            original = replay.record.receipt
            receipt = {
                "schema_version": "1.0",
                "candidate_id": source.candidate_id,
                "snapshot_sha256": source.snapshot_sha256,
                "replay_sha256": replay.replay_sha256,
                "record_sha256": replay.record_sha256,
                "kind": replay.kind,
                "original_run_id": original.run_id,
                "original_agent_version": original.agent_version,
                "state": "ARCHIVED_INPUTS_ONLY",
                "promotion_allowed": False,
            }
        elif args.command == "snapshot":
            snapshot = workspace.snapshot(args.candidate_id)
            receipt = {
                "schema_version": "1.0",
                "candidate_id": snapshot.candidate_id,
                "candidate_commit": snapshot.candidate_commit,
                "rollback_commit": snapshot.rollback_commit,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "agent_id": snapshot.proposed_manifest["agent_id"],
                "proposed_version": snapshot.proposed_manifest["agent_version"],
                "inventory": snapshot.inventory,
                "state": "SNAPSHOT_ONLY",
                "promotion_allowed": False,
            }
        elif plan is None:
            receipt = workspace.inspect(args.candidate_id)
        else:
            receipt = workspace.prepare(
                plan.incident_id,
                new_version=plan.new_version,
                replacements=plan.replacements,
                diagnosis=plan.diagnosis,
                regression_plan=tuple(plan.regression_plan),
            )
        print(json.dumps(receipt, sort_keys=True))
        return (
            1
            if args.command == "execute" and (receipt["exit_code"] != 0 or receipt["oom_killed"])
            else 0
        )
    except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired):
        parser.exit(
            2,
            "Repair request rejected; check journal, plan, source boundaries, candidate integrity and retained container leases.\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())
