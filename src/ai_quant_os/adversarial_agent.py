"""A11 independently checks retained native stress evidence without candidate execution."""

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import (
    CompiledContext,
    EvidenceRecord,
    SourceBinding,
    verify_context,
)
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.research_results import ResearchReport
from ai_quant_os.research_store import encode_research

INPUT_NAMES = ("strategy_candidate", "validation_results", "stress_library")


class CandidateObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["strategy_candidate"] = "strategy_candidate"
    experiment_id: str
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_ref: str


class ValidationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["validation_results"] = "validation_results"
    experiment_id: str
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["FAILED", "REJECTED", "VALIDATION_INCOMPLETE"]
    gates: tuple[tuple[str, str, str], ...] = Field(min_length=1, max_length=16)


class StressScenarioObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    net_pnl: Decimal = Field(allow_inf_nan=False)
    pnl_currency: Literal["USD", "JPY"]
    native_order_count: int = Field(strict=True, ge=0, le=4096)


class StressObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["stress_library"] = "stress_library"
    experiment_id: str
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str = Field(min_length=1, max_length=128)
    scenarios: tuple[StressScenarioObservation, ...] = Field(min_length=1, max_length=16)


class AdversarialScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    net_pnl: Decimal = Field(allow_inf_nan=False)
    pnl_currency: Literal["USD", "JPY"]
    native_order_count: int = Field(strict=True, ge=0, le=4096)


class AdversarialScorecard(AgentEnvelope):
    agent_id: Literal["A11"] = "A11"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["AdversarialScorecard"] = "AdversarialScorecard"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    candidate_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    genome_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    adversarial_score: Literal["PASS", "FAIL", "PENDING"]
    assessment: Literal["FAILURE_FOUND", "NO_FAILURE_FOUND", "EVIDENCE_PENDING"]
    scenarios: tuple[AdversarialScenarioResult, ...] = Field(min_length=1, max_length=16)
    rejection_reasons: tuple[str, ...] = Field(max_length=16)
    experiment_id: str = Field(min_length=1, max_length=64)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    promotion_allowed: Literal[False] = False

    @classmethod
    def from_context(cls, context: CompiledContext, receipt: dict[str, object]):
        observations = _observations(context)
        candidate, validation, stress = observations
        scenarios = tuple(
            AdversarialScenarioResult.model_validate(row) for row in receipt.get("scenarios", ())
        )
        if (
            receipt
            != {
                "experiment_id": candidate.experiment_id,
                "report_sha256": candidate.report_sha256,
                "state": stress.state,
                "reason": stress.reason,
                "scenarios": [row.model_dump(mode="json") for row in stress.scenarios],
                "status": "CHECKED_ARCHIVED_STRESS",
            }
            or [row.model_dump(mode="json") for row in scenarios]
            != [row.model_dump(mode="json") for row in stress.scenarios]
            or validation.experiment_id != candidate.experiment_id
        ):
            raise ValueError("Adversarial stress receipt mismatch")
        reasons = (stress.reason,) if stress.state != "PASS" else ()
        return cls(
            task_id=context.task_id,
            input_refs=context.input_refs,
            evidence_refs=context.input_refs,
            created_at=context.created_at,
            valid_until=context.valid_until,
            trace_id=str(uuid4()),
            contradictions=tuple(
                f"{name}:{reason}" for name, state, reason in validation.gates if state == "FAIL"
            ),
            candidate_fingerprint=candidate.candidate_fingerprint,
            genome_fingerprint=candidate.genome_sha256,
            adversarial_score=stress.state,
            assessment={
                "PASS": "NO_FAILURE_FOUND",
                "FAIL": "FAILURE_FOUND",
                "PENDING": "EVIDENCE_PENDING",
            }[stress.state],
            scenarios=scenarios,
            rejection_reasons=reasons,
            experiment_id=candidate.experiment_id,
            report_sha256=candidate.report_sha256,
        )

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        candidate, validation, stress = _observations(context)
        expected_assessment = {
            "PASS": "NO_FAILURE_FOUND",
            "FAIL": "FAILURE_FOUND",
            "PENDING": "EVIDENCE_PENDING",
        }[stress.state]
        if (
            self.candidate_fingerprint,
            self.genome_fingerprint,
            self.adversarial_score,
            self.assessment,
            self.experiment_id,
            self.report_sha256,
            tuple(row.model_dump(mode="json") for row in self.scenarios),
            self.rejection_reasons,
            self.contradictions,
        ) != (
            candidate.candidate_fingerprint,
            candidate.genome_sha256,
            stress.state,
            expected_assessment,
            candidate.experiment_id,
            candidate.report_sha256,
            tuple(row.model_dump(mode="json") for row in stress.scenarios),
            (stress.reason,) if stress.state != "PASS" else (),
            tuple(
                f"{name}:{reason}" for name, state, reason in validation.gates if state == "FAIL"
            ),
        ):
            raise ValueError("Adversarial output differs from retained source evidence")


def _observations(context: CompiledContext):
    verify_context(context)
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A11", "0.2.0", "SANDBOX", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple((row.source_tool, row.resource_class) for row in context.inputs)
        != (
            ("RESEARCH_MEMORY_READ", "GENERAL"),
            ("RESEARCH_MEMORY_READ", "GENERAL"),
            ("HOLDOUT_READ", "HOLDOUT"),
        )
    ):
        raise ValueError("Adversarial validator requires its exact sandbox source contract")
    candidate, validation, stress = (
        CandidateObservation.model_validate_json(context.inputs[0].payload_json),
        ValidationObservation.model_validate_json(context.inputs[1].payload_json),
        StressObservation.model_validate_json(context.inputs[2].payload_json),
    )
    report_identity = f"research-report:{candidate.experiment_id}:{candidate.report_sha256}:"
    if (
        candidate.as_of != validation.as_of
        or candidate.as_of != stress.as_of
        or candidate.experiment_id != validation.experiment_id
        or candidate.experiment_id != stress.experiment_id
        or candidate.report_sha256 != validation.report_sha256
        or candidate.report_sha256 != stress.report_sha256
        or not candidate.as_of
        <= context.created_at
        < context.valid_until
        <= candidate.as_of + timedelta(seconds=5)
        or not validation.gates
        or next((row[:2] for row in validation.gates if row[0] == "ADVERSARIAL"), None)
        != ("ADVERSARIAL", stress.state)
        or tuple(row.source_id for row in context.inputs)
        != tuple(report_identity + name for name in INPUT_NAMES)
    ):
        raise ValueError("Adversarial source observations disagree")
    return candidate, validation, stress


def adversarial_sources(report: ResearchReport, *, now: datetime) -> dict[str, SourceBinding]:
    if (
        type(report) is not ResearchReport
        or report.schema_version != "1.2"
        or report.selection is None
        or report.adversarial is None
        or report.outcome == "FAILED"
    ):
        raise ValueError("Verified native adversarial evidence is required")
    from pydantic import TypeAdapter

    report = TypeAdapter(ResearchReport).validate_json(encode_research(report))
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("Aware observation time required")
    digest = sha256(encode_research(report)).hexdigest()
    candidate = CandidateObservation(
        as_of=now,
        experiment_id=report.plan.experiment_id,
        report_sha256=digest,
        genome_sha256=report.selection.genome.fingerprint,
        candidate_fingerprint=report.holdout.binding.implementation_sha256,
        strategy_ref=report.selection.genome.strategy_ref,
    )
    validation = ValidationObservation(
        as_of=now,
        experiment_id=report.plan.experiment_id,
        report_sha256=digest,
        outcome=report.outcome,
        gates=tuple((row.name, row.state, row.reason) for row in report.gates),
    )
    stress = StressObservation(
        as_of=now,
        experiment_id=report.plan.experiment_id,
        report_sha256=digest,
        state=report.adversarial.state,
        reason=report.adversarial.reason,
        scenarios=tuple(
            StressScenarioObservation(
                name=row.name,
                dataset_fingerprint=row.dataset_fingerprint,
                net_pnl=row.run.result.net_pnl,
                pnl_currency=row.run.result.pnl_currency,
                native_order_count=row.run.result.native_order_count,
            )
            for row in report.adversarial.scenarios
        ),
    )
    payloads = {
        "strategy_candidate": (candidate, "RESEARCH_MEMORY_READ", "GENERAL"),
        "validation_results": (validation, "RESEARCH_MEMORY_READ", "GENERAL"),
        "stress_library": (stress, "HOLDOUT_READ", "HOLDOUT"),
    }
    sources = {}
    for name, (model, tool, resource) in payloads.items():
        record = EvidenceRecord(
            source_id=f"research-report:{report.plan.experiment_id}:{digest}:{name}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=model.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, resource, fetch)
    return sources


async def inspect_adversarial(invocation: AgentInvocation) -> AdversarialScorecard:
    context = invocation.context
    candidate, _, _ = _observations(context)
    receipt = await invocation.tools.call(
        "STRESS_RUN",
        {"experiment_id": candidate.experiment_id, "report_sha256": candidate.report_sha256},
    )
    return AdversarialScorecard.from_context(context, receipt)


def adversarial_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A11", "0.2.0").manifest_ref, AdversarialScorecard, inspect_adversarial
    )
