"""Source-bound training execution in a fresh, offline worker container."""

import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext, _context_ref, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_genomes import AgentGenome, capture_baseline_genome, require_genome_child
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_protocols import (
    EvaluationProtocol,
    EvolutionDataset,
    require_protocol_genome,
)
from ai_quant_os.repair_sandbox import local_docker_command
from ai_quant_os.repair_image_inputs import read_repair_image_input

IMAGE_ID = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_FILES = 2048
MAX_PACKAGE_BYTES = 32 * 1024 * 1024


class EvolutionExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    agent_id: Literal["A02", "A07"]
    agent_version: str
    manifest: dict[str, object]
    sources: dict[str, str]
    cases: tuple[dict[str, object], ...] = Field(min_length=1, max_length=128)
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False


class GenomeExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    genome_ref: str
    dataset_ref: str
    outputs: tuple[dict[str, object], ...] = Field(min_length=1, max_length=128)
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False


def build_execution_request(
    *,
    genome: AgentGenome,
    dataset: EvolutionDataset,
    protocol: EvaluationProtocol,
    contexts: tuple[CompiledContext, ...] | None = None,
) -> EvolutionExecutionRequest:
    """Bind only full, historical TRAINING contexts to the exact genome and protocol."""
    return _build_execution_request(
        genome=genome,
        dataset=dataset,
        protocol=protocol,
        contexts=contexts,
        resource_class="TRAINING",
    )


def build_holdout_execution_request(
    *,
    genome: AgentGenome,
    dataset: EvolutionDataset,
    protocol: EvaluationProtocol,
    contexts: tuple[CompiledContext, ...] | None = None,
) -> EvolutionExecutionRequest:
    """Bind only full, historical HOLDOUT contexts to the exact genome and protocol.

    The independent-evaluation counterpart of :func:`build_execution_request`:
    identical binding discipline, but the dataset and every context input must
    carry the HOLDOUT resource class, so a genome can be executed on data its
    training and selection never saw.
    """
    return _build_execution_request(
        genome=genome,
        dataset=dataset,
        protocol=protocol,
        contexts=contexts,
        resource_class="HOLDOUT",
    )


def _build_execution_request(
    *,
    genome: AgentGenome,
    dataset: EvolutionDataset,
    protocol: EvaluationProtocol,
    contexts: tuple[CompiledContext, ...] | None = None,
    resource_class: Literal["TRAINING", "HOLDOUT"],
) -> EvolutionExecutionRequest:
    if type(dataset) is not EvolutionDataset or dataset.resource_class != resource_class:
        raise ValueError(
            "Evolution execution accepts a training dataset only"
            if resource_class == "TRAINING"
            else "Independent holdout execution accepts a holdout dataset only"
        )
    if type(genome) is not AgentGenome or type(protocol) is not EvaluationProtocol:
        raise ValueError("Typed genome and retained protocol are required")
    require_protocol_genome(protocol, genome)
    if (
        dataset.agent_id != genome.manifest.agent_id
        or protocol.dataset_ref != dataset.dataset_ref
        or protocol.agent_id != dataset.agent_id
    ):
        raise ValueError("Execution artifact identities differ")
    if contexts is None:
        parsed = tuple(
            CompiledContext.model_validate_json(case.input_json) for case in dataset.cases
        )
    else:
        parsed = contexts
    if len(parsed) != len(dataset.cases):
        raise ValueError("Context inventory differs from training cases")
    rows = []
    for case, context in zip(dataset.cases, parsed, strict=True):
        original = CompiledContext.model_validate_json(case.input_json)
        if context != original:
            raise ValueError("Context differs from its retained training case")
        if (
            context.agent_id != genome.manifest.agent_id
            or context.agent_version != "0.2.0"
            or context.scope != "SANDBOX"
            or context.status != "READY"
            or not context.inputs
            or any(row.resource_class != resource_class for row in context.inputs)
        ):
            raise ValueError(
                "Every execution context must be ready and training-only"
                if resource_class == "TRAINING"
                else "Every holdout execution context must be ready and holdout-only"
            )
        # Context identity is rebound to the immutable genome; evidence payloads remain byte-for-byte.
        rebound = context.model_copy(
            update={
                "agent_version": genome.manifest.agent_version,
                "manifest_ref": genome.manifest.manifest_ref,
                "context_ref": "pending",
            }
        )
        rebound = rebound.model_copy(update={"context_ref": _context_ref(rebound)})
        verify_context(rebound)
        rows.append({"case_id": case.case_id, "context": rebound.model_dump(mode="json")})
    sources = {row.path: row.content for row in genome.sources}
    request = EvolutionExecutionRequest(
        genome_ref=genome.genome_ref,
        protocol_sha256=protocol.protocol_sha256,
        dataset_ref=dataset.dataset_ref,
        agent_id=genome.manifest.agent_id,
        agent_version=genome.manifest.agent_version,
        manifest=genome.manifest.model_dump(mode="json"),
        sources=sources,
        cases=tuple(rows),
    )
    raw = request.model_dump_json().encode()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Evolution execution request exceeds 4 MiB")
    return request


def _snapshot_package(source: Path, destination: Path) -> None:
    """Copy only bounded ordinary package files; refuse symlinks/reparse points."""
    root_stat = source.lstat()
    if (
        stat.S_ISLNK(root_stat.st_mode)
        or getattr(root_stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        or not stat.S_ISDIR(root_stat.st_mode)
    ):
        raise ValueError("Evolution source package must be an ordinary directory")
    count = total = 0
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in directories:
            node = current_path / name
            metadata = node.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                or not stat.S_ISDIR(metadata.st_mode)
            ):
                raise ValueError("Evolution source package cannot contain linked directories")
        for name in filenames:
            source_file = current_path / name
            metadata = source_file.lstat()
            count += 1
            if (
                count > MAX_PACKAGE_FILES
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                or metadata.st_nlink != 1
            ):
                raise ValueError("Evolution source package has an unsafe or excessive file")
            limit = min(metadata.st_size, MAX_PACKAGE_BYTES)
            raw = read_repair_image_input(source_file, limit)
            total += len(raw)
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("Evolution source package exceeds its 32 MiB bound")
            target = destination / source_file.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Evolution worker emitted a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Evolution worker emitted non-finite JSON number: {value}")


_WORKER = r"""import asyncio, contextlib, importlib, json, sys
from pathlib import Path
sys.path.insert(0, "/job/src")
from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.graph_runtime import AgentInvocation

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
module_name, function_name = {
    "A02": ("ai_quant_os.regime_agent", "analyze_regimes"),
    "A07": ("ai_quant_os.integrity_agent", "assess_integrity"),
}[request["agent_id"]]
function = getattr(importlib.import_module(module_name), function_name)
async def evaluate():
    results = []
    for row in request["cases"]:
        context = CompiledContext.model_validate(row["context"])
        verify_context(context)
        with contextlib.redirect_stdout(sys.stderr):
            output = await asyncio.wait_for(function(AgentInvocation(context, None)), timeout=10)
        if not hasattr(output, "model_dump"):
            raise TypeError("Candidate returned no typed output")
        results.append({"case_id": row["case_id"], "output": output.model_dump(mode="json")})
    return results
payload = {"genome_ref": request["genome_ref"], "dataset_ref": request["dataset_ref"],
           "outputs": asyncio.run(evaluate()), "execution_verified": False,
           "promotion_allowed": False}
print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
"""


def _require_runnable_lineage(genome: AgentGenome, baseline: AgentGenome | None) -> None:
    """Only the captured server-owned baseline, or its direct challenger, may run."""
    if genome.parent_ref is None:
        expected = capture_baseline_genome(
            load_agent_catalog(), genome.manifest.agent_id, genome.objective
        )
        if genome != expected or baseline is not None:
            raise ValueError("Only the captured server-owned baseline can run as a root")
    elif baseline is None:
        raise ValueError("Challenger execution requires its exact parent baseline")
    else:
        require_genome_child(baseline, genome)
        if (
            baseline.parent_ref is not None
            or baseline.manifest.agent_id != genome.manifest.agent_id
        ):
            raise ValueError("Challenger parent must be the captured root genome")
        expected = capture_baseline_genome(
            load_agent_catalog(), baseline.manifest.agent_id, baseline.objective
        )
        if baseline != expected:
            raise ValueError("Challenger parent is not the captured server-owned baseline")


@dataclass(frozen=True)
class EvolutionRunner:
    """Execute one genome per ephemeral container; output never carries authority."""

    image_id: str = IMAGE_ID

    def __post_init__(self):
        if self.image_id != IMAGE_ID:
            raise ValueError("Evolution runner image is pinned by server policy")

    def run(
        self,
        genome: AgentGenome,
        dataset: EvolutionDataset,
        protocol: EvaluationProtocol,
        *,
        baseline: AgentGenome | None = None,
    ) -> GenomeExecutionResult:
        _require_runnable_lineage(genome, baseline)
        request = build_execution_request(genome=genome, dataset=dataset, protocol=protocol)
        return self._execute(request, genome, dataset)

    def run_holdout(
        self,
        genome: AgentGenome,
        dataset: EvolutionDataset,
        protocol: EvaluationProtocol,
        *,
        baseline: AgentGenome | None = None,
    ) -> GenomeExecutionResult:
        """Execute one genome on a HOLDOUT dataset under the same lineage rules.

        This is the execution half of independent quality evaluation: the
        genome runs in the same sealed worker, but every case and context
        input must carry the HOLDOUT resource class, so the measured outputs
        cannot come from data the genome was trained or selected on.
        """
        _require_runnable_lineage(genome, baseline)
        request = build_holdout_execution_request(genome=genome, dataset=dataset, protocol=protocol)
        return self._execute(request, genome, dataset)

    def _execute(
        self,
        request: EvolutionExecutionRequest,
        genome: AgentGenome,
        dataset: EvolutionDataset,
    ) -> GenomeExecutionResult:
        package_root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="ai-quant-evolution-") as temporary:
            job = Path(temporary)
            _snapshot_package(package_root, job / "src" / "ai_quant_os")
            for source in genome.sources:
                target = job / Path(source.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.content, encoding="utf-8", newline="")
            (job / "worker.py").write_text(_WORKER, encoding="utf-8", newline="")
            (job / "request.json").write_bytes(request.model_dump_json().encode("utf-8"))
            docker = shutil.which("docker")
            if docker is None:
                raise RuntimeError("Pinned local Docker engine is unavailable")
            owner = "ai-quant-evolution-" + uuid4().hex
            try:
                result = local_docker_command(
                    docker,
                    "run",
                    "--rm",
                    "--name",
                    owner,
                    "--network=none",
                    "--read-only",
                    "--user=65534:65534",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true",
                    "--memory=536870912",
                    "--memory-swap=536870912",
                    "--cpus=1",
                    "--pids-limit=64",
                    "--ulimit=cpu=30:30",
                    "--ulimit=fsize=1048576:1048576",
                    "--ulimit=nofile=128:128",
                    "--ulimit=nproc=64:64",
                    "--tmpfs=/scratch:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
                    "--workdir=/scratch",
                    "--volume",
                    f"{job}:/job:ro",
                    "--entrypoint=/usr/local/bin/python",
                    self.image_id,
                    "-I",
                    "-B",
                    "/job/worker.py",
                    "/job/request.json",
                    timeout=40,
                )
            finally:
                cleanup = local_docker_command(docker, "rm", "--force", owner, timeout=10)
                if cleanup.returncode != 0:
                    message = cleanup.stderr.decode("utf-8", errors="replace")
                    if "No such container" not in message and "no such container" not in message:
                        raise RuntimeError("Evolution worker container cleanup is uncertain")
            if result.returncode != 0:
                reason = result.stderr.decode("utf-8", errors="replace")[:4000]
                raise RuntimeError(f"Evolution worker failed ({result.returncode}): {reason}")
            payload = json.loads(
                result.stdout,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            if not isinstance(payload, dict):
                raise ValueError("Evolution worker output must be a JSON object")
            if (
                payload.get("genome_ref") != genome.genome_ref
                or payload.get("dataset_ref") != dataset.dataset_ref
            ):
                raise ValueError("Evolution worker output identity mismatch")
            if (
                payload.get("execution_verified") is not False
                or payload.get("promotion_allowed") is not False
            ):
                raise ValueError("Evolution worker attempted to claim authority")
            outputs = tuple(payload.get("outputs", ()))
            if tuple(row.get("case_id") for row in outputs) != tuple(
                case.case_id for case in dataset.cases
            ):
                raise ValueError("Evolution worker output inventory mismatch")
            validated_outputs = []
            for row, case in zip(outputs, request.cases, strict=True):
                if set(row) != {"case_id", "output"} or not isinstance(row["output"], dict):
                    raise ValueError("Evolution worker returned an invalid output envelope")
                output = _validate_output_contract(
                    row["output"],
                    CompiledContext.model_validate(case["context"]),
                    genome.manifest.agent_id,
                    genome.manifest.agent_version,
                )
                validated_outputs.append({"case_id": row["case_id"], "output": output})
            return GenomeExecutionResult(
                genome_ref=genome.genome_ref,
                dataset_ref=dataset.dataset_ref,
                outputs=tuple(validated_outputs),
            )


def _validate_output_contract(
    raw: dict[str, object], context: CompiledContext, agent_id: str, version: str
) -> dict[str, object]:
    """Check the fixed mission envelope and the owned A02/A07 top-level contract."""
    output_type, specialized = (
        (
            "RegimeDistribution",
            {"policy_version", "model_route", "calibration_status", "assessments"},
        )
        if agent_id == "A02"
        else (
            "DataHealth",
            {
                "status",
                "instruments",
                "quarantined_instruments",
                "ingress_counts",
                "clock_status",
                "ingress_retention",
                "external_feed_coverage",
                "incident_request",
            },
        )
        if agent_id == "A07"
        else (None, set())
    )
    expected_keys = set(AgentEnvelope.model_fields) | specialized | {"output_type"}
    if output_type is None or set(raw) != expected_keys:
        raise ValueError("Evolution output differs from the fixed agent output shape")
    common = {key: raw[key] for key in AgentEnvelope.model_fields}
    envelope = AgentEnvelope.model_validate(common)
    if (
        raw["output_type"] != output_type
        or (envelope.agent_id, envelope.agent_version) != (agent_id, version)
        or envelope.task_id != context.task_id
        or envelope.input_refs != context.input_refs
        or not set(envelope.evidence_refs).issubset(context.input_refs)
        or envelope.created_at < context.created_at
        or envelope.valid_until > context.valid_until
    ):
        raise ValueError("Evolution output identity or context binding mismatch")
    if agent_id == "A02":
        if (
            raw["policy_version"] != "paper-multi-window-v1"
            or raw["model_route"] != "DETERMINISTIC_ONLY"
            or raw["calibration_status"] != "UNAVAILABLE"
            or not isinstance(raw["assessments"], list)
            or len(raw["assessments"]) > 16
        ):
            raise ValueError("Regime output contract mismatch")
        seen_instruments = set()
        for assessment in raw["assessments"]:
            if (
                not isinstance(assessment, dict)
                or set(assessment) != {"instrument_id", "horizons", "event_status", "crisis_status"}
                or not isinstance(assessment["instrument_id"], str)
                or assessment["instrument_id"] in seen_instruments
                or assessment["event_status"] != "UNAVAILABLE"
                or assessment["crisis_status"] != "UNAVAILABLE"
                or not isinstance(assessment["horizons"], list)
                or len(assessment["horizons"]) != 3
            ):
                raise ValueError("Regime instrument output shape mismatch")
            seen_instruments.add(assessment["instrument_id"])
            for horizon, expected_name in zip(
                assessment["horizons"], ("SHORT", "MEDIUM", "LONG"), strict=True
            ):
                if (
                    not isinstance(horizon, dict)
                    or set(horizon)
                    != {
                        "horizon",
                        "status",
                        "bar_count",
                        "observed_span_seconds",
                        "regime",
                        "distribution",
                        "distribution_kind",
                    }
                    or horizon["horizon"] != expected_name
                    or horizon["status"] not in {"AVAILABLE", "INSUFFICIENT", "UNAVAILABLE"}
                    or type(horizon["bar_count"]) is not int
                    or not 0 <= horizon["bar_count"] <= 64
                    or horizon["regime"]
                    not in {"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}
                    or horizon["distribution_kind"] != "UNCALIBRATED_FEATURE_SCORES"
                    or not isinstance(horizon["distribution"], dict)
                    or set(horizon["distribution"])
                    != {"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}
                ):
                    raise ValueError("Regime horizon output shape mismatch")
                try:
                    scores = tuple(
                        Decimal(str(value)) for value in horizon["distribution"].values()
                    )
                except (InvalidOperation, ValueError):
                    raise ValueError("Regime distribution must contain finite decimals") from None
                if (
                    any(not score.is_finite() or not 0 <= score <= 1 for score in scores)
                    or sum(scores) != 1
                ):
                    raise ValueError("Regime distribution is invalid")
                observed_span = horizon["observed_span_seconds"]
                if observed_span is not None:
                    try:
                        span = Decimal(str(observed_span))
                    except (InvalidOperation, ValueError):
                        raise ValueError("Regime evidence span is invalid") from None
                    if not span.is_finite() or span < 0:
                        raise ValueError("Regime evidence span is invalid")
    elif (
        raw["status"] not in {"UNUSABLE", "DEGRADED", "HEALTHY"}
        or not isinstance(raw["instruments"], list)
        or not isinstance(raw["quarantined_instruments"], list)
        or not isinstance(raw["ingress_counts"], dict)
        or raw["clock_status"] not in {"UNVERIFIED", "MEASURED"}
        or raw["ingress_retention"] != "PROCESS_LOCAL_SINCE_STARTUP"
        or raw["external_feed_coverage"] != "UNAVAILABLE"
    ):
        raise ValueError("Integrity output contract mismatch")
    else:
        instruments = raw["instruments"]
        if len(instruments) > 16 or len(raw["quarantined_instruments"]) > 16:
            raise ValueError("Integrity output exceeds instrument bounds")
        identifiers = []
        for finding in instruments:
            if (
                not isinstance(finding, dict)
                or set(finding) != {"instrument_id", "usable", "issues", "affected_features"}
                or not isinstance(finding["instrument_id"], str)
                or type(finding["usable"]) is not bool
                or not _string_list(finding["issues"])
                or not _string_list(finding["affected_features"])
            ):
                raise ValueError("Integrity finding output shape mismatch")
            identifiers.append(finding["instrument_id"])
        if len(set(identifiers)) != len(identifiers) or raw["quarantined_instruments"] != [
            row["instrument_id"] for row in instruments if not row["usable"]
        ]:
            raise ValueError("Integrity quarantine inventory mismatch")
        if any(
            not isinstance(code, str) or type(count) is not int or not 0 <= count <= 2**31 - 1
            for code, count in raw["ingress_counts"].items()
        ):
            raise ValueError("Integrity ingress counts are invalid")
        from ai_quant_os.ingress_health import CODES

        if set(raw["ingress_counts"]) != CODES:
            raise ValueError("Integrity ingress code inventory mismatch")
        incident = raw["incident_request"]
        if incident is not None and incident != {
            "requested_mode": "NO_NEW_RISK",
            "automatic_control_change": False,
            "reason": "UNUSABLE_MARKET_EVIDENCE",
        }:
            raise ValueError("Integrity incident request violates its fixed authority")
        if bool(raw["quarantined_instruments"]) != (incident is not None):
            raise ValueError("Integrity incident request differs from quarantine evidence")
    return raw


def _string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 64
        and all(isinstance(item, str) and 0 < len(item) <= 256 for item in value)
    )
