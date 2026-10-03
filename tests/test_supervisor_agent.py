from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.agent_context import CompiledContext
from ai_quant_os.supervisor_agent import TaskPlan
from test_supervisor_api import NOW, HEADERS, execute


@pytest.fixture
def recorded_plan(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        receipt = execute(client)
        raw = client.get(
            "/api/v1/agents/contexts/" + receipt["context_ref"], headers=HEADERS
        ).json()
        context = CompiledContext.model_validate(raw["checkpoint"]["context"])
        return json.loads(receipt["output_json"]), context


@pytest.mark.parametrize("claim", ["omission", "world", "health", "parent", "deferred"])
def test_plan_requires_exact_source_selection_and_parent_binding(recorded_plan, claim):
    raw, context = recorded_plan
    if claim == "omission":
        raw["selected"] = raw["selected"][1:]
        raw["calls"] = raw["calls"][1:]
    elif claim == "world":
        raw["world_state_version"] = "world:" + "0" * 64
    elif claim == "health":
        raw["health_status"] = "MONITORED"
    elif claim == "parent":
        raw["parent_context_ref"] = "agent-context:" + "0" * 64
        raw["selected"] = []
        raw["calls"] = []
    else:
        raw["deferred"] = []
    with pytest.raises(ValueError):
        plan = TaskPlan.model_validate(raw)
        plan.require_context(context)


@pytest.mark.parametrize("claim", ["run_id", "hash", "expiry", "call_version", "confidence"])
def test_typed_plan_rejects_impossible_receipt_and_authority_claims(recorded_plan, claim):
    raw, context = recorded_plan
    if claim == "run_id":
        raw["calls"][0]["run_id"] = "supervisor_" + "0" * 64
    elif claim == "hash":
        raw["calls"][0]["output_sha256"] = None
    elif claim == "expiry":
        raw["calls"][0]["valid_until"] = (NOW + timedelta(seconds=1)).isoformat()
    elif claim == "call_version":
        raw["calls"][0]["agent_version"] = "0.1.0"
    else:
        raw["confidence"] = "0.9"
    with pytest.raises(ValueError):
        TaskPlan.model_validate(raw)
