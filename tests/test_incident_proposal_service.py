import asyncio
from threading import Event
from datetime import timedelta

import pytest

from ai_quant_os.agent_registry import ToolInvocation, load_agent_catalog
from ai_quant_os.incident_agent import proposal_for_context
from ai_quant_os.incident_proposal_api import IncidentPropose
from ai_quant_os.incident_proposal_store import IncidentProposalStore
from ai_quant_os.risk import SafetyState
from test_incident_sources import compile_context
from test_incident_api import NOW


def invocation(context, **changes):
    return ToolInvocation(
        **{
            **dict(
                agent_id="A18",
                agent_version="0.2.0",
                tool="INCIDENT_PROPOSE",
                scope="PAPER",
                payload={},
                context_ref=context.context_ref,
                task_id=context.task_id,
            ),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"context_ref": None},
        {"task_id": "other"},
        {"agent_id": "A07"},
        {"scope": "SANDBOX"},
        {"payload": {"recommended_state": "NORMAL"}},
    ],
)
def test_forged_or_unbound_proposal_authority_is_denied(tmp_path, changes):
    async def check():
        context = await compile_context()
        record = proposal_for_context(context)
        store = IncidentProposalStore(tmp_path / "proposals.sqlite3")
        try:
            service = IncidentPropose(
                store,
                load_agent_catalog(),
                context=lambda ref: context,
                clock=lambda: NOW,
                risk_states=lambda: (SafetyState.NORMAL, SafetyState.NORMAL),
            )
            with pytest.raises(PermissionError):
                await service(invocation(context, **changes))
            assert store.get(record.proposal_ref) is None
        finally:
            store.close()

    asyncio.run(check())


def test_current_higher_risk_floor_denies_publication_without_control_change(tmp_path):
    async def check():
        context = await compile_context()
        store = IncidentProposalStore(tmp_path / "proposals.sqlite3")
        try:
            service = IncidentPropose(
                store,
                load_agent_catalog(),
                context=lambda ref: context,
                clock=lambda: NOW,
                risk_states=lambda: (SafetyState.HALT, SafetyState.HALT),
            )
            with pytest.raises(ValueError, match="exceeds"):
                await service(invocation(context))
            assert store.get(proposal_for_context(context).proposal_ref) is None
        finally:
            store.close()

    asyncio.run(check())


def test_expiry_during_actual_publication_withholds_reply_but_retains_archival_record(tmp_path):
    async def check():
        context = await compile_context()
        moment = [NOW]

        class ExpiringStore(IncidentProposalStore):
            def put(self, proposal):
                record = super().put(proposal)
                moment[0] = NOW + timedelta(seconds=5)
                return record

        store = ExpiringStore(tmp_path / "proposals.sqlite3")
        try:
            service = IncidentPropose(
                store,
                load_agent_catalog(),
                context=lambda ref: context,
                clock=lambda: moment[0],
                risk_states=lambda: (SafetyState.NORMAL, SafetyState.NORMAL),
            )
            with pytest.raises(ValueError, match="expired"):
                await service(invocation(context))
            record = store.get(proposal_for_context(context).proposal_ref)
            assert record is not None and not record.workflow_dispatched
        finally:
            store.close()

    asyncio.run(check())


@pytest.mark.parametrize("state", ["LIMITED", "QUARANTINED"])
def test_revocation_during_publication_withholds_reply_and_preserves_archive(tmp_path, state):
    async def check():
        context = await compile_context()
        registry = load_agent_catalog()

        class RevokingStore(IncidentProposalStore):
            def put(self, proposal):
                record = super().put(proposal)
                registry.set_lifecycle("A18", "0.2.0", state)
                return record

        store = RevokingStore(tmp_path / "proposals.sqlite3")
        try:
            service = IncidentPropose(
                store,
                registry,
                context=lambda ref: context,
                clock=lambda: NOW,
                risk_states=lambda: (SafetyState.NORMAL, SafetyState.NORMAL),
            )
            with pytest.raises(PermissionError):
                await service(invocation(context))
            assert store.get(proposal_for_context(context).proposal_ref) is not None
        finally:
            store.close()

    asyncio.run(check())


def test_cancellation_joins_owned_publication_before_returning(tmp_path):
    async def check():
        context = await compile_context()
        started, release, finished = Event(), Event(), Event()

        class PausedStore(IncidentProposalStore):
            def put(self, proposal):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Test publication release missing")
                record = super().put(proposal)
                finished.set()
                return record

        store = PausedStore(tmp_path / "proposals.sqlite3")
        try:
            service = IncidentPropose(
                store,
                load_agent_catalog(),
                context=lambda ref: context,
                clock=lambda: NOW,
                risk_states=lambda: (SafetyState.NORMAL, SafetyState.NORMAL),
            )
            task = asyncio.create_task(service(invocation(context)))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
            proposal = store.get(proposal_for_context(context).proposal_ref)
            assert proposal is not None and not proposal.control_authorized
        finally:
            release.set()
            store.close()

    asyncio.run(check())
