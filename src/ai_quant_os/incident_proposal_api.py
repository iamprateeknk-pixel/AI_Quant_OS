"""Runtime-bound local incident proposal publication and protected archival reads."""

from datetime import datetime
import sqlite3
from typing import Callable

from fastapi import Depends, HTTPException

from ai_quant_os.agent_context import CompiledContext, require_context_for_agent
from ai_quant_os.agent_registry import AgentRegistry, ToolInvocation
from ai_quant_os.incident_agent import proposal_for_context
from ai_quant_os.incident_proposal_store import IncidentProposalStore
from ai_quant_os.health_watchdog import owned_health_call
from ai_quant_os.risk import SafetyState


class IncidentPropose:
    def __init__(
        self,
        store: IncidentProposalStore,
        registry: AgentRegistry,
        *,
        context: Callable[[str], CompiledContext],
        clock: Callable[[], datetime],
        risk_states: Callable[[], tuple[SafetyState, SafetyState]],
    ):
        self._store, self._registry, self._context = store, registry, context
        self._clock, self._risk_states = clock, risk_states

    async def __call__(self, invocation: ToolInvocation) -> dict[str, object]:
        if (
            (invocation.agent_id, invocation.agent_version, invocation.scope, invocation.tool)
            != ("A18", "0.2.0", "PAPER", "INCIDENT_PROPOSE")
            or invocation.payload
            or not invocation.context_ref
            or not invocation.task_id
        ):
            raise PermissionError("Unsupported incident proposal authority")
        context = self._context(invocation.context_ref)
        if context.context_ref != invocation.context_ref:
            raise PermissionError("Incident context identity mismatch")
        require_context_for_agent(
            context,
            registry=self._registry,
            agent_id="A18",
            agent_version="0.2.0",
            task_id=invocation.task_id,
            scope="PAPER",
            now=self._clock(),
        )
        self._registry.require_tool("A18", "0.2.0", "INCIDENT_PROPOSE", scope="PAPER")
        proposal = proposal_for_context(context)
        states = list(SafetyState)
        if any(
            states.index(state) > states.index(proposal.recommended_state)
            for state in self._risk_states()
        ):
            raise ValueError("Current safety state exceeds incident observation recommendation")
        stored = await owned_health_call(self._store.put, proposal)
        require_context_for_agent(
            context,
            registry=self._registry,
            agent_id="A18",
            agent_version="0.2.0",
            task_id=invocation.task_id,
            scope="PAPER",
            now=self._clock(),
        )
        self._registry.require_tool("A18", "0.2.0", "INCIDENT_PROPOSE", scope="PAPER")
        if any(
            states.index(state) > states.index(proposal.recommended_state)
            for state in self._risk_states()
        ):
            raise ValueError("Safety state changed during archival proposal publication")
        return {"proposal_ref": stored.proposal_ref, "status": stored.status}


def install_incident_proposal_routes(app, require_operator, store: IncidentProposalStore):
    @app.get(
        "/api/v1/paper/incidents/proposals/{proposal_ref}", dependencies=[Depends(require_operator)]
    )
    def archived_proposal(proposal_ref: str):
        try:
            proposal = store.get(proposal_ref)
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Incident proposal storage unavailable") from error
        if proposal is None:
            raise HTTPException(404, "Incident proposal not found")
        return {
            "mode": "PAPER",
            "archival_only": True,
            "proposal_ref": proposal.proposal_ref,
            "proposal": proposal.model_dump(mode="json"),
        }
