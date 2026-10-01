import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.agent_context import (
    ContextCompiler,
    EvidenceRecord,
    SourceBinding,
    require_current_context,
)
from ai_quant_os.agent_registry import load_agent_catalog

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def binding(tool="WORLD_STATE_READ", resource_class="GENERAL", changes=None):
    async def fetch():
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=3),
            trusted=True,
            payload_json='{"value":1}',
            **(changes or {}),
        )

    return SourceBinding(tool=tool, resource_class=resource_class, fetch=fetch)


def compile_context(sources, agent="A02", scope="PAPER", **kwargs):
    return asyncio.run(
        ContextCompiler(load_agent_catalog(), sources, clock=lambda: NOW, **kwargs).compile(
            agent, "0.1.0", task_id="fixture", scope=scope
        )
    )


def test_context_is_reproducible_provenance_bound_and_reports_missing_inputs():
    result = compile_context({"world_state": binding()})
    assert result == compile_context({"world_state": binding()})
    assert result.status == "DEGRADED"
    assert result.inputs[0].status == "AVAILABLE"
    assert result.inputs[0].payload_json == '{"value":1}'
    assert result.input_refs == (result.inputs[0].content_ref,)
    assert result.valid_until == NOW + timedelta(seconds=3)
    assert all(item.status == "MISSING" for item in result.inputs[1:])
    assert result.manifest_ref == load_agent_catalog().get("A02", "0.1.0").manifest_ref
    require_current_context(result, now=NOW)
    with pytest.raises(ValueError, match="expired"):
        require_current_context(result, now=NOW + timedelta(seconds=3))


@pytest.mark.parametrize("agent", ["A08", "A09", "A16", "A17"])
def test_holdout_cannot_leak_through_generic_source_capability(agent):
    registry = load_agent_catalog()
    manifest = registry.get(agent, "0.1.0")
    key = manifest.required_inputs[0]
    calls = []

    async def fetch():
        calls.append(1)
        raise AssertionError("Holdout source must never be fetched")

    source = SourceBinding(tool="WORLD_STATE_READ", resource_class="HOLDOUT", fetch=fetch)
    result = asyncio.run(
        ContextCompiler(registry, {key: source}, clock=lambda: NOW).compile(
            agent, "0.1.0", task_id="fixture", scope="SANDBOX"
        )
    )
    assert result.inputs[0].status == "DENIED"
    assert result.inputs[0].payload_json is None
    assert result.inputs[0].content_ref is None
    assert calls == []


def test_validator_holdout_is_sandbox_only_and_keeps_source_identity():
    result = compile_context(
        {"immutable_datasets": binding("HOLDOUT_READ", "HOLDOUT")}, agent="A10", scope="SANDBOX"
    )
    item = next(row for row in result.inputs if row.input_name == "immutable_datasets")
    assert item.status == "AVAILABLE"
    assert item.resource_class == "HOLDOUT"
    denied = compile_context(
        {"immutable_datasets": binding("HOLDOUT_READ", "HOLDOUT")}, agent="A10"
    )
    assert (
        next(row for row in denied.inputs if row.input_name == "immutable_datasets").status
        == "DENIED"
    )


@pytest.mark.parametrize("kind", ["STALE", "FUTURE", "UNTRUSTED", "TIMEOUT", "FAILED"])
def test_bad_sources_are_withheld_without_exception_details(kind):
    async def fetch():
        if kind == "TIMEOUT":
            await asyncio.sleep(10)
        if kind == "FAILED":
            raise RuntimeError("private-source-secret")
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW + timedelta(seconds=1) if kind == "FUTURE" else NOW - timedelta(seconds=5),
            valid_until=NOW if kind == "STALE" else NOW + timedelta(seconds=3),
            trusted=kind != "UNTRUSTED",
            payload_json='{"value":1}',
        )

    result = compile_context(
        {
            "world_state": SourceBinding(
                tool="WORLD_STATE_READ", resource_class="GENERAL", fetch=fetch
            )
        },
        timeout_seconds=0.01,
    )
    item = result.inputs[0]
    assert item.status == kind
    assert item.payload_json is None
    assert "private-source-secret" not in result.model_dump_json()


def test_source_expiring_while_other_inputs_load_is_withheld():
    time = [NOW]

    async def first():
        return EvidenceRecord(
            source_id="first",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=1),
            trusted=True,
            payload_json="{}",
        )

    async def second():
        time[0] = NOW + timedelta(seconds=2)
        return EvidenceRecord(
            source_id="second",
            as_of=time[0],
            valid_until=time[0] + timedelta(seconds=3),
            trusted=True,
            payload_json="{}",
        )

    result = asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            {
                "world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", first),
                "price_features": SourceBinding("WORLD_STATE_READ", "GENERAL", second),
            },
            clock=lambda: time[0],
        ).compile("A02", "0.1.0", task_id="fixture", scope="PAPER")
    )
    assert result.inputs[0].status == "STALE"
    assert result.inputs[0].payload_json is None


def test_context_budget_withholds_oversized_payload_and_tampering_is_rejected():
    async def fetch():
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=3),
            trusted=True,
            payload_json='{"data":"' + "x" * 9000 + '"}',
        )

    result = compile_context({"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)})
    assert result.inputs[0].status == "BUDGET_EXCEEDED"
    valid = compile_context({"world_state": binding()})
    edited = valid.model_copy(update={"task_id": "tampered"})
    with pytest.raises(ValueError, match="integrity"):
        require_current_context(edited, now=NOW)


def test_quarantined_agent_never_fetches_and_unknown_inputs_are_not_exposed():
    registry = load_agent_catalog()
    registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
    calls = []

    async def fetch():
        calls.append(1)
        raise AssertionError("Quarantined source")

    compiler = ContextCompiler(
        registry,
        {
            "world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch),
            "private_other_agent": binding(),
        },
        clock=lambda: NOW,
    )
    result = asyncio.run(compiler.compile("A02", "0.1.0", task_id="fixture", scope="PAPER"))
    assert result.inputs[0].status == "DENIED"
    assert "private_other_agent" not in result.model_dump_json()
    assert calls == []


def test_api_context_uses_server_evidence_and_rejects_client_sources():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    url = "/api/v1/paper/agents/context"
    payload = {"agent_id": "A02", "agent_version": "0.1.0", "task_id": "fixture"}
    assert client.post(url, json=payload).status_code == 401
    headers = {"X-Operator-Token": "fixture-token"}
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "DEGRADED"
    assert response.json()["requires_escalation"] is True
    assert (
        client.post(
            url, json={**payload, "sources": {"holdout": "private"}}, headers=headers
        ).status_code
        == 422
    )
    assert client.get("/api/v1/orders").json()["orders"] == []


def test_overall_deadline_and_caller_cancellation_clean_up_source_tasks():
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(1)

    sources = {
        name: SourceBinding("WORLD_STATE_READ", "GENERAL", slow)
        for name in load_agent_catalog().get("A02", "0.1.0").required_inputs
    }

    async def invoke():
        compiler = ContextCompiler(
            load_agent_catalog(),
            sources,
            clock=lambda: NOW,
            timeout_seconds=1,
            overall_timeout_seconds=0.01,
            max_concurrency=1,
        )
        result = await compiler.compile("A02", "0.1.0", task_id="fixture", scope="PAPER")
        assert all(row.status == "TIMEOUT" for row in result.inputs)
        assert len(cancelled) == 1
        cancelled.clear()
        task = asyncio.create_task(
            compiler.compile("A02", "0.1.0", task_id="fixture", scope="PAPER")
        )
        await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cancelled) == 1

    asyncio.run(invoke())


def test_total_budget_is_enforced_without_silently_truncating_payloads():
    async def fetch():
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=3),
            trusted=True,
            payload_json='{"text":"' + "x" * 2500 + '"}',
        )

    source = SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
    result = compile_context(
        {name: source for name in load_agent_catalog().get("A02", "0.1.0").required_inputs},
        max_context_bytes=4096,
    )
    assert len(result.model_dump_json().encode()) <= 4096
    assert any(row.status == "BUDGET_EXCEEDED" for row in result.inputs)
    assert all(len(row.payload_json) > 2500 for row in result.inputs if row.status == "AVAILABLE")


def test_permission_revocation_during_fetch_withholds_returned_data():
    registry = load_agent_catalog()

    async def fetch():
        registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
        return EvidenceRecord(
            source_id="fixture",
            as_of=NOW,
            valid_until=NOW + timedelta(seconds=3),
            trusted=True,
            payload_json="{}",
        )

    result = asyncio.run(
        ContextCompiler(
            registry,
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
        ).compile("A02", "0.1.0", task_id="fixture", scope="PAPER")
    )
    assert result.inputs[0].status == "DENIED"
    assert result.input_refs == ()


def test_api_withholds_context_expiring_during_audit(monkeypatch):
    from ai_quant_os.paper_state_store import PaperStateStore

    time = [NOW]
    original = PaperStateStore.record_event

    def delayed(self, key, event):
        original(self, key, event)
        if event["action"] == "PAPER_AGENT_CONTEXT":
            time[0] = NOW + timedelta(seconds=6)

    monkeypatch.setattr(PaperStateStore, "record_event", delayed)
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: time[0]))
    result = client.post(
        "/api/v1/paper/agents/context",
        headers={"X-Operator-Token": "fixture-token"},
        json={"agent_id": "A02", "agent_version": "0.1.0", "task_id": "fixture"},
    )
    assert result.status_code == 409
    assert "expired" in result.json()["detail"]


def test_consumer_rechecks_exact_task_manifest_and_current_permissions():
    from ai_quant_os.agent_context import require_context_for_agent

    registry = load_agent_catalog()
    context = asyncio.run(
        ContextCompiler(registry, {"world_state": binding()}, clock=lambda: NOW).compile(
            "A02", "0.1.0", task_id="fixture", scope="PAPER"
        )
    )
    expected = {
        "registry": registry,
        "agent_id": "A02",
        "agent_version": "0.1.0",
        "task_id": "fixture",
        "scope": "PAPER",
        "now": NOW,
    }
    require_context_for_agent(context, **expected)
    with pytest.raises(PermissionError):
        require_context_for_agent(context, **{**expected, "task_id": "another-task"})
    with pytest.raises(PermissionError):
        require_context_for_agent(context, **{**expected, "agent_id": "A03"})
    registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")
    with pytest.raises(PermissionError):
        require_context_for_agent(context, **expected)
