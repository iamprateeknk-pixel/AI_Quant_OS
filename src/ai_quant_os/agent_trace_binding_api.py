"""Protected original-run context lookup under a verified local audit prefix."""

from datetime import datetime, timezone
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, Header, Path, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_trace_binding import AgentTraceBinding
from ai_quant_os.agent_runs import AgentRun
from ai_quant_os.contracts import _require_aware
from ai_quant_os.paper_state_store import PaperStateStore

RUN_PATTERN = r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$"


class AgentTraceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    delivery_verified: Literal[False] = False
    independently_attested: Literal[False] = False
    run_id: str = Field(pattern=RUN_PATTERN)
    observed_at: AwareDatetime
    state: Literal["AVAILABLE", "NOT_RECORDED", "NOT_FOUND", "UNAVAILABLE"]
    source_head: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_event_count: int | None = Field(
        default=None, strict=True, ge=0, le=9_007_199_254_740_991
    )
    binding: AgentTraceBinding | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC lookup time required")
        if self.state == "UNAVAILABLE":
            if (
                self.source_head is not None
                or self.source_event_count is not None
                or self.binding is not None
            ):
                raise ValueError("Unavailable lookup cannot infer a prefix or binding")
            return self
        if self.source_head is None or self.source_event_count is None:
            raise ValueError("Original verified prefix required")
        if self.state == "AVAILABLE":
            if (
                self.binding is None
                or self.binding.run_id != self.run_id
                or self.binding.recorded_at > self.observed_at
                or self.source_event_count < 2
            ):
                raise ValueError("Original recorded binding required")
        elif (
            self.binding is not None or self.state == "NOT_RECORDED" and self.source_event_count < 1
        ):
            raise ValueError("Missing binding cannot infer SDK context")
        return self


def observe_agent_trace(
    store: PaperStateStore, run_id: str, clock: Callable[[], datetime]
) -> AgentTraceObservation:
    try:
        source = store.agent_trace_observation(run_id)
    except Exception:
        source = None
    now = clock()
    _require_aware(now)
    now = now.astimezone(timezone.utc)
    try:
        if source is None:
            raise ValueError("Original lookup source unavailable")
        run, binding, head = source
        if run is not None:
            run = AgentRun.model_validate_json(run.model_dump_json())
        if (
            head.valid is not True
            or head.issue is not None
            or run is not None
            and (run.run_id != run_id or run.recorded_at > now)
        ):
            raise ValueError("Original lookup source differs")
        if binding is not None:
            if run is None:
                raise ValueError("Original run absent")
            binding = AgentTraceBinding.model_validate_json(binding.model_dump_json())
            binding.require_run(run)
        return AgentTraceObservation(
            run_id=run_id,
            observed_at=now,
            state="AVAILABLE"
            if binding is not None
            else "NOT_RECORDED"
            if run is not None
            else "NOT_FOUND",
            source_head=head.head_hash,
            source_event_count=head.event_count,
            binding=binding,
        )
    except Exception:
        return AgentTraceObservation(run_id=run_id, observed_at=now, state="UNAVAILABLE")


def install_agent_trace_binding_routes(
    app: FastAPI, store: PaperStateStore, require_operator: Callable, clock: Callable[[], datetime]
):
    @app.get(
        "/api/v1/observability/agent-runs/{run_id}/trace-binding",
        response_model=AgentTraceObservation,
        dependencies=[Depends(require_operator)],
    )
    def read(
        run_id: Annotated[str, Path(pattern=RUN_PATTERN)],
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        observation = observe_agent_trace(store, run_id, clock)
        require_operator(x_operator_token, x_operator_session)
        response.headers["Cache-Control"] = "no-store"
        return observation
