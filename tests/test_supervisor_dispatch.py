import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from ai_quant_os.agent_context import ContextCompiler
from ai_quant_os.agent_registry import AgentToolBoundary, ToolInvocation, load_agent_catalog
from ai_quant_os.graph_runtime import AgentImplementation, GraphRuntime
from ai_quant_os.integrity_agent import integrity_implementation, integrity_sources
from ai_quant_os.ingress_health import IngressHealth
from ai_quant_os.supervisor_agent import child_run_id, supervisor_implementation
from ai_quant_os.supervisor_dispatch import SupervisorDispatch
from ai_quant_os.supervisor_sources import supervisor_sources
from ai_quant_os.world_state import WorldStateEngine
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from test_supervisor_sources import NOW


@asynccontextmanager
async def harness(tmp_path, *, invoke=None):
    registry = load_agent_catalog()
    moment = [NOW]
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    parent = await ContextCompiler(
        registry,
        supervisor_sources(world, registry),
        clock=lambda: moment[0],
        max_input_bytes=65536,
        max_context_bytes=160000,
    ).compile("A01", "0.2.0", task_id="dispatch", scope="PAPER")
    compiled = []
    admissions = []

    async def compile_child(agent, task):
        compiled.append(agent)
        return await ContextCompiler(
            registry,
            integrity_sources(world, {}, IngressHealth().snapshot()),
            clock=lambda: moment[0],
        ).compile(agent, "0.2.0", task_id=task, scope="PAPER")

    async def admission():
        admissions.append(True)

    runtime_holder = []
    dispatcher = SupervisorDispatch(
        registry,
        runtime=lambda: runtime_holder[0],
        context=lambda ref: parent,
        compile_child=compile_child,
        admission=admission,
    )
    implementation = integrity_implementation(registry)
    if invoke:
        implementation = AgentImplementation(
            implementation.manifest_ref, implementation.output_schema, invoke
        )
    async with GraphRuntime.open(
        tmp_path / "graphs.sqlite3",
        registry=registry,
        implementations={
            ("A01", "0.2.0"): supervisor_implementation(registry),
            ("A07", "0.2.0"): implementation,
        },
        tools=AgentToolBoundary(registry, {"TASK_ORCHESTRATE": dispatcher}),
        clock=lambda: moment[0],
    ) as runtime:
        runtime_holder.append(runtime)
        yield dispatcher, runtime, parent, registry, compiled, moment, admissions


def invocation(parent, **changes):
    return ToolInvocation(
        **{
            **dict(
                agent_id="A01",
                agent_version="0.2.0",
                tool="TASK_ORCHESTRATE",
                scope="PAPER",
                payload={},
                context_ref=parent.context_ref,
                task_id=parent.task_id,
            ),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"context_ref": None},
        {"task_id": "other"},
        {"agent_id": "A02"},
        {"scope": "SANDBOX"},
        {"payload": {"context_ref": "forged"}},
        {"payload": {"agent_calls": ["A09"]}},
    ],
)
def test_dispatch_refuses_unbound_or_forged_authority_before_any_call(tmp_path, changes):
    async def check():
        async with harness(tmp_path) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            with pytest.raises(PermissionError):
                await dispatcher(invocation(parent, **changes))
            assert compiled == [] and admissions == []

    asyncio.run(check())


def test_concurrent_dispatch_reuses_exact_child_receipts_without_recompilation(tmp_path):
    async def check():
        async with harness(tmp_path) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            first, second = await asyncio.gather(
                dispatcher(invocation(parent)), dispatcher(invocation(parent))
            )
            assert first == second and compiled == ["A07", "A06", "A05", "A02", "A04", "A03"]
            assert first["calls"][0]["status"] == "COMPLETED"
            assert runtime.receipt(child_run_id(parent.context_ref, "A07")).status == "COMPLETED"

    asyncio.run(check())


def test_permission_revocation_after_snapshot_prevents_specialist_call(tmp_path):
    async def check():
        async with harness(tmp_path) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            registry.quarantine("A07", "0.2.0", registry.get("A07", "0.2.0").manifest_ref)
            result = await dispatcher(invocation(parent))
            assert result["calls"][0]["status"] == "NOT_STARTED_PERMISSION"
            assert "A07" not in compiled
            with pytest.raises(KeyError):
                runtime.receipt(child_run_id(parent.context_ref, "A07"))

    asyncio.run(check())


def test_cancelled_child_is_uncertain_and_never_invoked_again(tmp_path):
    entered = asyncio.Event()
    invocations = []

    async def slow(invocation):
        invocations.append(invocation.context.context_ref)
        entered.set()
        await asyncio.Event().wait()

    async def check():
        async with harness(tmp_path, invoke=slow) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            task = asyncio.create_task(dispatcher(invocation(parent)))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            run_id = child_run_id(parent.context_ref, "A07")
            assert runtime.receipt(run_id).status == "RUNNING"
            result = await dispatcher(invocation(parent))
            assert result["calls"][0]["status"] == "UNCERTAIN" and len(invocations) == 1
            assert runtime.receipt(run_id).output_json is None

    asyncio.run(check())


def test_expired_or_limited_parent_cannot_start_calls(tmp_path):
    async def check():
        async with harness(tmp_path) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            registry.set_lifecycle("A01", "0.2.0", "LIMITED")
            with pytest.raises(PermissionError):
                await dispatcher(invocation(parent))
            registry.set_lifecycle("A01", "0.2.0", "SHADOW")
            moment[0] = NOW + timedelta(seconds=5)
            with pytest.raises(ValueError):
                await dispatcher(invocation(parent))
            assert compiled == []

    asyncio.run(check())


def test_parent_deadline_stops_after_expired_child_and_retains_both_failures(tmp_path):
    async def check():
        moment_ref = []

        async def expire(invocation):
            from ai_quant_os.integrity_agent import assess_integrity

            output = await assess_integrity(invocation)
            moment_ref[0][0] = NOW + timedelta(seconds=5)
            return output

        async with harness(tmp_path, invoke=expire) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            moment_ref.append(moment)
            receipt = await runtime.run("deadline-parent", parent)
            assert receipt.status == "EXPIRED" and receipt.output_json is None
            assert compiled == ["A07"]
            child = runtime.receipt(child_run_id(parent.context_ref, "A07"))
            assert child.status == "EXPIRED" and child.output_json is None

    asyncio.run(check())


def test_child_timeout_remains_typed_and_escalated_in_completed_parent(tmp_path):
    async def slow(invocation):
        await asyncio.Event().wait()

    async def check():
        async with harness(tmp_path, invoke=slow) as (
            dispatcher,
            runtime,
            parent,
            registry,
            compiled,
            moment,
            admissions,
        ):
            receipt = await runtime.run("timeout-parent", parent)
            assert receipt.status == "COMPLETED"
            import json

            output = json.loads(receipt.output_json)
            assert output["calls"][0]["status"] == "TIMEOUT"
            assert "A07_TIMEOUT" in output["contradictions"]
            assert output["requires_escalation"] and output["confidence"] == "0"

    asyncio.run(check())
