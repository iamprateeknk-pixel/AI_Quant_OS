"""Read-only evidence projections for retained counterfactual reports."""

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.counterfactual_results import CounterfactualReport
from ai_quant_os.research_store import encode_research

INPUT_NAMES = ("replay_datasets", "decisions", "fills", "world_state_archives")


class CounterfactualVariantObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    pnl_difference: Decimal = Field(allow_inf_nan=False)


class CounterfactualReportObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    replay_id: str = Field(min_length=1, max_length=64)
    available: StrictBool
    report_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    candidate_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["COMPLETED", "FAILED", "UNAVAILABLE"]
    variants: tuple[CounterfactualVariantObservation, ...] = Field(max_length=8)
    promotion_allowed: Literal[False] = False


class UnavailableCounterfactualEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    input_name: Literal["decisions", "fills", "world_state_archives"]
    availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal[
        "DECISION_LINEAGE_NOT_LINKED",
        "FILL_LINEAGE_NOT_LINKED",
        "WORLD_STATE_ARCHIVE_NOT_LINKED",
    ]


def counterfactual_sources(
    report: CounterfactualReport | None, *, replay_id: str, now: datetime
) -> dict[str, SourceBinding]:
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or not isinstance(replay_id, str)
        or not replay_id
        or len(replay_id) > 64
        or (report is not None and type(report) is not CounterfactualReport)
    ):
        raise ValueError("Current retained counterfactual evidence required")
    if report is None:
        observation = CounterfactualReportObservation(
            as_of=now,
            replay_id=replay_id,
            available=False,
            outcome="UNAVAILABLE",
            variants=(),
        )
    else:
        if report.inputs.plan.replay_id != replay_id:
            raise ValueError("Counterfactual report identity mismatch")
        raw = encode_research(report)
        observation = CounterfactualReportObservation(
            as_of=now,
            replay_id=replay_id,
            available=True,
            report_sha256=sha256(raw).hexdigest(),
            candidate_id=report.inputs.plan.candidate_id,
            outcome=report.outcome,
            variants=tuple(
                CounterfactualVariantObservation(
                    name=row.variant.name,
                    pnl_difference=row.pnl_difference,
                )
                for row in report.results
                if row.variant.name != "BASELINE"
            ),
        )
    payloads = {
        "replay_datasets": (observation, "ATTRIBUTION_READ"),
        "decisions": (
            UnavailableCounterfactualEvidence(
                as_of=now, input_name="decisions", reason="DECISION_LINEAGE_NOT_LINKED"
            ),
            "HISTORY_READ",
        ),
        "fills": (
            UnavailableCounterfactualEvidence(
                as_of=now, input_name="fills", reason="FILL_LINEAGE_NOT_LINKED"
            ),
            "PORTFOLIO_READ",
        ),
        "world_state_archives": (
            UnavailableCounterfactualEvidence(
                as_of=now,
                input_name="world_state_archives",
                reason="WORLD_STATE_ARCHIVE_NOT_LINKED",
            ),
            "HISTORY_READ",
        ),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        encoded = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        record = EvidenceRecord(
            source_id=f"counterfactual-review:{name}:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
