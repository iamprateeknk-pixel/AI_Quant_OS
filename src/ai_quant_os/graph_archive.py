"""Public metadata projection for a bounded snapshot of original graph runs."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.graph_ledger import GraphRecord, RunStatus
from ai_quant_os.graph_trace import trace_identity


class GraphArchiveChanged(ValueError):
    pass


class GraphRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    agent_id: str = Field(pattern=r"^A(?:0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    task_id: str = Field(min_length=1, max_length=128)
    scope: Literal["PAPER", "SANDBOX"]
    status: RunStatus
    context_ref: str = Field(pattern=r"^agent-context:[a-f0-9]{64}$")
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    trace_id: str = Field(pattern=r"^graph-trace:[a-f0-9]{64}$")
    context_observed_at: AwareDatetime
    context_valid_until: AwareDatetime

    @model_validator(mode="after")
    def binding(self):
        if self.trace_id != trace_identity(self.run_id, self.context_ref, self.manifest_ref):
            raise ValueError("Graph summary trace identity mismatch")
        if any(
            moment.utcoffset().total_seconds() != 0
            for moment in (self.context_observed_at, self.context_valid_until)
        ):
            raise ValueError("Graph summary observation requires UTC")
        if self.context_valid_until <= self.context_observed_at:
            raise ValueError("Graph summary context expiry mismatch")
        return self


def summary(record: GraphRecord) -> GraphRunSummary:
    receipt = record.receipt
    return GraphRunSummary(
        **{
            name: getattr(receipt, name)
            for name in (
                "run_id",
                "agent_id",
                "agent_version",
                "task_id",
                "status",
                "context_ref",
                "manifest_ref",
            )
        },
        scope=record.checkpoint.context.scope,
        trace_id=trace_identity(receipt.run_id, receipt.context_ref, receipt.manifest_ref),
        context_observed_at=receipt.created_at,
        context_valid_until=receipt.valid_until,
    )


class GraphRunPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    archival_only: Literal[True] = True
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    total: int = Field(strict=True, ge=0, le=10000)
    after_run_id: str = Field(max_length=128, pattern=r"^(?:[A-Za-z0-9_-]{1,128})?$")
    remaining: int = Field(strict=True, ge=0, le=10000)
    next_after: str | None = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    records: tuple[GraphRunSummary, ...] = Field(max_length=25)

    @model_validator(mode="after")
    def page_binding(self):
        identities = [row.run_id for row in self.records]
        if identities != sorted(set(identities)) or any(
            value <= self.after_run_id for value in identities
        ):
            raise ValueError("Graph page identity order mismatch")
        if self.remaining and not identities:
            raise ValueError("Graph page cannot omit remaining records")
        expected = identities[-1] if self.remaining else None
        if self.next_after != expected or self.total < len(identities) + self.remaining + bool(
            self.after_run_id
        ):
            raise ValueError("Graph page cursor/count mismatch")
        if not self.after_run_id and self.total != len(identities) + self.remaining:
            raise ValueError("Graph page total mismatch")
        return self
