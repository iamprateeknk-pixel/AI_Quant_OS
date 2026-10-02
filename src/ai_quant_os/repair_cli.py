"""Local, proposal-only repair candidate commands; never executes replacement code."""

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_store import HealthStore
from ai_quant_os.repair_workspace import RepairWorkspace
from ai_quant_os.repair_sandbox import DockerConfinement


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
    sandbox = commands.add_parser(
        "sandbox-status", help="Read local sandbox prerequisites; execute nothing"
    )
    sandbox.add_argument("--image-id", required=True)
    sandbox.add_argument("--suite-sha256", required=True)
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
        if args.command == "snapshot":
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
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired):
        parser.exit(
            2,
            "Repair request rejected; check journal, plan, source boundaries and candidate integrity.\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())
