"""Archive, inspect and export agent genomes; no evaluation or promotion command."""

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from ai_quant_os.agent_genomes import (
    AgentGenome,
    EvaluationObjective,
    MAX_GENOME_BYTES,
    encode_genome,
)
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.genome_store import GenomeStore
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
                raise ValueError("Immutable genome export differs") from None
    finally:
        pending.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inert agent genomes and lineage; no release authority"
    )
    parser.add_argument("--workspace", type=Path, default=Path("data-local/evolution"))
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("agent_id")
    seed.add_argument("--objective", type=Path, required=True)
    register = commands.add_parser("register")
    register.add_argument("--input", type=Path, required=True)
    commands.add_parser("list")
    lineage = commands.add_parser("lineage")
    lineage.add_argument("genome_ref")
    export = commands.add_parser("export")
    export.add_argument("genome_ref")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.workspace.resolve()
        if args.command == "seed":
            objective = EvaluationObjective.model_validate_json(
                read_repair_image_input(args.objective, 8192)
            )
        elif args.command == "register":
            genome = AgentGenome.model_validate_json(
                read_repair_image_input(args.input, MAX_GENOME_BYTES)
            )
        elif args.command == "export":
            output = args.output.resolve()
            if output.is_relative_to(root) or output.suffix.lower() != ".json":
                raise ValueError("Export requires a JSON path outside registry storage")
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
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(
            2,
            "Genome operation refused; check input, lineage, storage integrity and output path.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
