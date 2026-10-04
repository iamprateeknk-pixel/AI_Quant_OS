import asyncio
from threading import Event

import pytest

from ai_quant_os.agent_registry import ToolInvocation, load_agent_catalog
from ai_quant_os.strategy_build_store import StrategyBuildStore
from ai_quant_os.strategy_builder_service import CandidateCreate
from ai_quant_os.strategy_builder_sources import candidate_for_context
from test_strategy_builder_sources import context
from test_research_hypothesis_api import NOW


def invocation(compiled, **changes):
    return ToolInvocation(
        **{
            **dict(
                agent_id="A09",
                agent_version="0.2.0",
                scope="SANDBOX",
                tool="CANDIDATE_CREATE",
                payload={},
                task_id=compiled.task_id,
                context_ref=compiled.context_ref,
            ),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "PAPER"},
        {"task_id": "other"},
        {"agent_id": "A08"},
        {"context_ref": None},
        {"payload": {"code": "invented"}},
    ],
)
def test_candidate_tool_rejects_client_source_and_forged_task_authority(tmp_path, changes):
    compiled = context()
    candidate = candidate_for_context(compiled)
    with StrategyBuildStore(tmp_path / "build.sqlite3") as store:
        store.put_review(candidate.review)
        service = CandidateCreate(
            store, load_agent_catalog(), context=lambda ref: compiled, clock=lambda: NOW
        )
        with pytest.raises((PermissionError, ValueError)):
            asyncio.run(service(invocation(compiled, **changes)))
        assert store.get_candidate(candidate.candidate_ref) is None


def test_limited_agent_cannot_publish_candidate_even_with_an_operator_review(tmp_path):
    compiled = context()
    candidate = candidate_for_context(compiled)
    registry = load_agent_catalog()
    registry.set_lifecycle("A09", "0.2.0", "LIMITED")
    with StrategyBuildStore(tmp_path / "build.sqlite3") as store:
        store.put_review(candidate.review)
        service = CandidateCreate(store, registry, context=lambda ref: compiled, clock=lambda: NOW)
        with pytest.raises(PermissionError):
            asyncio.run(service(invocation(compiled)))
        assert store.get_candidate(candidate.candidate_ref) is None


def test_cancelled_publication_joins_owned_write_and_keeps_only_inert_artifact(
    tmp_path, monkeypatch
):
    compiled = context()
    candidate = candidate_for_context(compiled)
    entered, release = Event(), Event()
    with StrategyBuildStore(tmp_path / "build.sqlite3") as store:
        store.put_review(candidate.review)
        original = store.put_candidate

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Controlled test write not released")
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "put_candidate", blocked)
        service = CandidateCreate(
            store, load_agent_catalog(), context=lambda ref: compiled, clock=lambda: NOW
        )

        async def check():
            task = asyncio.create_task(service(invocation(compiled)))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(check())
        saved = store.get_candidate(candidate.candidate_ref)
        assert (
            saved == candidate and not saved.execution_authorized and not saved.runtime_registered
        )
