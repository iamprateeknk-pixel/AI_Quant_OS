"""V3.2 section 11: real paper-system handlers behind the MCP registry.

Every success path is seeded through the real store or service the
handler reads (research catalog CSV publication, a risk-approved paper
order and fill on the real execution gateway, a recorded health
incident, an audit-chained paper event) -- never by mocking a handler.
"""

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import ai_quant_os.mcp_handlers as handlers_module
from ai_quant_os.broker import BrokerRouter, SimulatedBroker
from ai_quant_os.contracts import TradeIntent
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.mcp_api import paper_mcp_service
from ai_quant_os.mcp_handlers import McpToolBindings, build_tool_handlers
from ai_quant_os.mcp_tools import FORBIDDEN_TOOL_FRAGMENTS
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
from ai_quant_os.risk_policy import PaperRiskPolicy

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)

AGENT_TOOLS = {
    "A01": {
        "get_risk_state",
        "query_dataset",
        "get_exposure",
        "simulate_allocation",
        "stress_portfolio",
        "simulate_risk_change",
        "get_broker_health",
        "get_incident",
        "get_policy",
        "export_evidence",
        "deploy_shadow",
        "pause_shadow",
        "compare_challenger",
        "run_backtest",
        "build_strategy_artifact",
        "create_approval_request",
    },
    "A02": {"get_policy"},
}


def _risk() -> RiskKernel:
    return RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5))


def _gateway(tmp_path) -> PaperExecutionGateway:
    """Real gateway holding one risk-approved BUY 100 EURUSD @ 1.1000."""
    risk = _risk()
    gateway = PaperExecutionGateway(risk, store_path=tmp_path / "paper.sqlite3")
    intent = TradeIntent(
        intent_id="intent-1",
        strategy_id="demo",
        strategy_version="1",
        instrument_id="EURUSD",
        side="BUY",
        desired_exposure=Decimal("100"),
        risk_budget=Decimal("10"),
        max_spread=Decimal("0.0003"),
        max_slippage=Decimal("0.0002"),
        valid_until=NOW + timedelta(minutes=1),
        world_state_version="world-1",
        evidence_refs=("fixture:1",),
        created_at=NOW,
    )
    market = MarketSnapshot(
        instrument_id="EURUSD",
        bid=Decimal("1.1000"),
        ask=Decimal("1.1001"),
        observed_at=NOW,
        world_state_version="world-1",
        trusted=True,
    )
    decision = risk.evaluate(intent, market, now=NOW)
    assert decision.decision == "APPROVE"
    order = gateway.submit(intent, decision, idempotency_key="key-1", now=NOW)
    assert order is not None and order.status == "ACCEPTED"
    gateway.record_fill(
        fill_id="fill-1",
        order_id=order.order_id,
        quantity=Decimal("100"),
        price=Decimal("1.1000"),
        fee=Decimal("0"),
        observed_at=NOW,
    )
    return gateway


def _catalog(tmp_path) -> tuple[ResearchEvidenceCatalog, object]:
    catalog = ResearchEvidenceCatalog(tmp_path / "research.sqlite3", training_only=True)
    raw = (
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(f"EURUSD,2026-10-01T00:0{i}:00Z,1.1,1.1,1.1,1.1,1" for i in range(3))
        + "\n"
    ).encode()
    receipt = catalog.publish(
        raw, source_id="development", resource_class="TRAINING", received_at=NOW
    )
    return catalog, receipt


def _health_store(tmp_path) -> tuple[HealthStore, HealthIncident]:
    store = HealthStore(tmp_path / "health.sqlite3")
    incident = store.record(HealthIncident("QUOTE_STALE", "EURUSD", '{"source":"fixture"}', NOW))
    return store, incident


def _state_store(tmp_path) -> PaperStateStore:
    store = PaperStateStore(tmp_path / "state.sqlite3")
    store.record_event("evidence:1", {"action": "FIXTURE", "detail": "seeded"})
    return store


def _router() -> BrokerRouter:
    return BrokerRouter([SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)])


def _full_bindings(tmp_path) -> tuple[McpToolBindings, dict]:
    catalog, receipt = _catalog(tmp_path)
    health, incident = _health_store(tmp_path)
    bindings = McpToolBindings(
        research_catalog=catalog,
        execution=_gateway(tmp_path),
        broker_router=_router(),
        health_store=health,
        risk_policy=PaperRiskPolicy(),
        risk_kernel=_risk(),
        paper_state_store=_state_store(tmp_path),
    )
    return bindings, {"receipt": receipt, "incident": incident}


def _service(bindings: McpToolBindings | None):
    return paper_mcp_service(risk=_risk(), agent_tools=AGENT_TOOLS, bindings=bindings)


def test_query_dataset_returns_seeded_receipt(tmp_path):
    bindings, seeded = _full_bindings(tmp_path)
    service = _service(bindings)
    result = service.call_tool(
        "A01", "query_dataset", {"dataset_id": seeded["receipt"].dataset_ref}
    )["result"]
    assert result["mode"] == "PAPER"
    assert result["dataset"]["dataset_ref"] == seeded["receipt"].dataset_ref
    assert result["dataset"]["source_id"] == "development"
    assert result["dataset"]["resource_class"] == "TRAINING"
    assert result["dataset"]["row_count"] == 3
    with pytest.raises(LookupError):
        service.call_tool("A01", "query_dataset", {"dataset_id": "research-data:" + "0" * 64})


def test_get_exposure_reflects_seeded_fill(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    service = _service(bindings)
    result = service.call_tool("A01", "get_exposure")["result"]
    assert result["mode"] == "PAPER"
    assert result["order_count"] == 1
    assert result["fill_count"] == 1
    eurusd = next(row for row in result["instruments"] if row["instrument_id"] == "EURUSD")
    assert eurusd["filled_net_quantity"] == "100"
    assert Decimal(eurusd["cash_flow_quote"]) == Decimal("-110")


def test_simulate_allocation_is_pure_and_weighted(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    gateway = bindings.execution
    service = _service(bindings)
    before = (len(gateway.fills), gateway.exposure_snapshot.filled_net_quantity)
    result = service.call_tool(
        "A01",
        "simulate_allocation",
        {"weights": {"EURUSD": "0.5", "GBPUSD": "0.5"}},
    )["result"]
    assert result["mutates_state"] is False
    assert Decimal(result["current_gross_quantity"]) == Decimal("100")
    targets = {row["instrument_id"]: row for row in result["targets"]}
    assert Decimal(targets["EURUSD"]["target_net_quantity"]) == Decimal("50")
    assert Decimal(targets["EURUSD"]["delta_quantity"]) == Decimal("-50")
    assert Decimal(targets["GBPUSD"]["delta_quantity"]) == Decimal("50")
    # A JSON-string weights argument (the catalog schema type) works too.
    again = service.call_tool("A01", "simulate_allocation", {"weights": '{"EURUSD": "1"}'})[
        "result"
    ]
    assert Decimal(again["targets"][0]["target_net_quantity"]) == Decimal("100")
    after = (len(gateway.fills), gateway.exposure_snapshot.filled_net_quantity)
    assert after == before


def test_stress_portfolio_uses_seeded_fills_and_caller_shock(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    gateway = bindings.execution
    service = _service(bindings)
    result = service.call_tool("A01", "stress_portfolio", {"scenario": {"shock_bps": "-1000"}})[
        "result"
    ]
    assert result["mutates_state"] is False
    (row,) = result["instruments"]
    assert row["instrument_id"] == "EURUSD"
    assert row["mark_basis"] == "quantity_weighted_average_fill_price"
    # 100 units at VWAP 1.1000 shocked by -10% loses 11.00 quote units.
    assert Decimal(row["stressed_pnl_change_quote"]) == Decimal("-11")
    assert Decimal(result["total_stressed_pnl_change_quote"]) == Decimal("-11")
    assert len(gateway.fills) == 1
    with pytest.raises(ValueError):
        service.call_tool("A01", "stress_portfolio", {"scenario": {"note": "x"}})


def test_simulate_risk_change_never_applies(tmp_path):
    risk = _risk()
    bindings = McpToolBindings(risk_kernel=risk)
    service = paper_mcp_service(risk=risk, agent_tools=AGENT_TOOLS, bindings=bindings)
    result = service.call_tool(
        "A01", "simulate_risk_change", {"proposal": {"safety_state": "HALT"}}
    )["result"]
    assert result["applies_change"] is False
    assert result["current_safety_state"] == "NORMAL"
    assert result["safety_state_assessment"] == "ESCALATION_PERMITTED_BY_KERNEL_LADDER"
    assert risk.safety_state is SafetyState.NORMAL
    limited = service.call_tool(
        "A01",
        "simulate_risk_change",
        {"proposal": '{"max_portfolio_exposure": "2000"}'},
    )["result"]
    assert limited["current_max_portfolio_exposure"] == "1500"
    assert limited["limit_delta"] == "500"
    assert limited["limit_assessment"] == "REQUIRES_NEW_VERSIONED_RISK_POLICY"
    risk.set_safety_state(SafetyState.CAUTION)
    recovery = service.call_tool(
        "A01", "simulate_risk_change", {"proposal": {"safety_state": "NORMAL"}}
    )["result"]
    assert recovery["safety_state_assessment"] == "REQUIRES_GATED_PAPER_RECOVERY_WORKFLOW"
    assert risk.safety_state is SafetyState.CAUTION


def test_get_broker_health_returns_router_snapshot(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    service = _service(bindings)
    result = service.call_tool("A01", "get_broker_health", {"broker_id": "PAPER"})["result"]
    assert result["connection_state"] == "CONNECTED"
    assert result["enabled"] is True
    assert result["instruments"] == ["EURUSD"]
    assert result["eligibility"]["eligible"] is True
    assert result["eligibility"]["compliance_scope"] == "LOCAL_SIMULATION_ONLY"
    with pytest.raises(LookupError):
        service.call_tool("A01", "get_broker_health", {"broker_id": "NOPE"})


def test_get_incident_returns_seeded_incident(tmp_path):
    bindings, seeded = _full_bindings(tmp_path)
    service = _service(bindings)
    result = service.call_tool(
        "A01", "get_incident", {"incident_id": seeded["incident"].incident_id}
    )["result"]
    assert result["code"] == "QUOTE_STALE"
    assert result["subject"] == "EURUSD"
    assert result["safety_state"] == "NO_NEW_RISK"
    assert result["quarantine"] is False
    with pytest.raises(LookupError):
        service.call_tool("A01", "get_incident", {"incident_id": "0" * 64})


def test_get_policy_serves_the_versioned_paper_policy(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    service = _service(bindings)
    policy = PaperRiskPolicy()
    result = service.call_tool("A01", "get_policy", {"policy_id": "paper-risk-1.0.0"})["result"]
    assert result["policy_version"] == "paper-risk-1.0.0"
    assert result["digest"] == policy.digest
    assert result["max_order_exposure"] == "1000"
    assert result["max_portfolio_exposure"] == "1500"
    assert result["reporting_currency"] == "USD"
    with pytest.raises(LookupError):
        service.call_tool("A01", "get_policy", {"policy_id": "paper-risk-9.9.9"})


def test_export_evidence_returns_seeded_audit_event(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    service = _service(bindings)
    result = service.call_tool("A01", "export_evidence", {"evidence_id": "evidence:1"})["result"]
    assert result["event"] == {"action": "FIXTURE", "detail": "seeded"}
    assert result["audit_integrity"]["valid"] is True
    assert result["audit_integrity"]["event_count"] == 1
    with pytest.raises(LookupError):
        service.call_tool("A01", "export_evidence", {"evidence_id": "evidence:missing"})


def test_shadow_seam_approval_gating_and_argument_hygiene(tmp_path):
    calls: list[str] = []

    def deploy(candidate_id: str) -> dict:
        calls.append(candidate_id)
        return {"candidate_id": candidate_id, "state": "SHADOW_DEPLOYED"}

    bindings = McpToolBindings(deploy_shadow_fn=deploy)
    service = paper_mcp_service(risk=_risk(), agent_tools=AGENT_TOOLS, bindings=bindings)
    with pytest.raises(PermissionError):
        service.call_tool("A01", "deploy_shadow", {"candidate_id": "cand-1"})
    assert calls == []
    result = service.call_tool("A01", "deploy_shadow", {"candidate_id": "cand-1"}, approved=True)[
        "result"
    ]
    assert result["state"] == "SHADOW_DEPLOYED"
    # The approval marker is stripped by the registry before dispatch:
    # the service callable saw exactly the candidate id and nothing else.
    assert calls == ["cand-1"]


def test_compare_challenger_seam_delegates_ids(tmp_path):
    seen: list[tuple[str, str]] = []

    def compare(challenger_id: str, champion_id: str) -> dict:
        seen.append((challenger_id, champion_id))
        return {"winner": challenger_id}

    bindings = McpToolBindings(compare_challenger_fn=compare)
    service = paper_mcp_service(risk=_risk(), agent_tools=AGENT_TOOLS, bindings=bindings)
    result = service.call_tool(
        "A01",
        "compare_challenger",
        {"challenger_id": "chal-1", "champion_id": "champ-1"},
    )["result"]
    assert result["winner"] == "chal-1"
    assert seen == [("chal-1", "champ-1")]


def test_allow_list_still_enforced_with_handlers_bound(tmp_path):
    bindings, _ = _full_bindings(tmp_path)
    service = _service(bindings)
    with pytest.raises(PermissionError):
        service.call_tool("A02", "get_exposure")
    allowed = service.call_tool("A02", "get_policy", {"policy_id": "paper-risk-1.0.0"})
    assert allowed["result"]["policy_version"] == "paper-risk-1.0.0"


def test_unbound_tools_stay_unregistered_with_partial_bindings(tmp_path):
    bindings = McpToolBindings(execution=_gateway(tmp_path))
    service = _service(bindings)
    assert service.call_tool("A01", "get_exposure")["result"]["fill_count"] == 1
    for name, arguments in (
        ("query_dataset", {"dataset_id": "research-data:" + "0" * 64}),
        ("get_policy", {"policy_id": "paper-risk-1.0.0"}),
        ("run_backtest", {"strategy_version_id": "s", "dataset_version": "d"}),
        ("build_strategy_artifact", {"candidate_id": "c1"}),
        ("create_approval_request", {"action": "a", "reason": "r"}),
        ("pause_shadow", {"candidate_id": "c1"}),
        ("compare_challenger", {"challenger_id": "a", "champion_id": "b"}),
    ):
        with pytest.raises(LookupError):
            service.call_tool("A01", name, arguments)
    # Approved deploy with no deployment service bound still fails closed.
    with pytest.raises(LookupError):
        service.call_tool("A01", "deploy_shadow", {"candidate_id": "c1"}, approved=True)


def test_no_bindings_behaves_exactly_like_phase_two():
    service = paper_mcp_service(risk=_risk(), agent_tools=AGENT_TOOLS)
    assert service.call_tool("A01", "get_risk_state")["result"] == {
        "safety_state": "NORMAL",
        "mode": "PAPER",
    }
    for name, arguments in (
        ("get_exposure", {}),
        ("simulate_risk_change", {"proposal": {"safety_state": "HALT"}}),
        ("get_policy", {"policy_id": "paper-risk-1.0.0"}),
    ):
        with pytest.raises(LookupError):
            service.call_tool("A01", name, arguments)
    with pytest.raises(LookupError):
        service.call_tool("A01", "deploy_shadow", {"candidate_id": "c1"}, approved=True)


def test_read_and_simulate_handlers_do_not_mutate_sources(tmp_path):
    bindings, seeded = _full_bindings(tmp_path)
    service = _service(bindings)
    gateway = bindings.execution
    catalog = bindings.research_catalog
    before = (
        len(gateway.fills),
        gateway.exposure_snapshot.filled_net_quantity,
        len(catalog.list()),
        bindings.risk_kernel.safety_state,
    )
    service.call_tool("A01", "get_exposure")
    service.call_tool("A01", "simulate_allocation", {"weights": {"EURUSD": "1"}})
    service.call_tool("A01", "stress_portfolio", {"scenario": {"shock_bps": "250"}})
    service.call_tool("A01", "simulate_risk_change", {"proposal": {"safety_state": "HALT"}})
    service.call_tool("A01", "query_dataset", {"dataset_id": seeded["receipt"].dataset_ref})
    after = (
        len(gateway.fills),
        gateway.exposure_snapshot.filled_net_quantity,
        len(catalog.list()),
        bindings.risk_kernel.safety_state,
    )
    assert after == before


def test_handlers_module_has_no_order_placement_surface():
    source = inspect.getsource(handlers_module)
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines)
    for forbidden in (
        "ai_quant_os.broker",
        "ai_quant_os.execution",
        "ai_quant_os.order_store",
        "mt5",
        "nautilus",
    ):
        assert forbidden not in joined
    field_names = [field.name for field in dataclasses.fields(McpToolBindings)]
    for name in field_names:
        lowered = name.lower()
        assert "order" not in lowered
        assert "credential" not in lowered
        assert "secret" not in lowered
        assert "password" not in lowered
        assert not any(fragment in lowered for fragment in FORBIDDEN_TOOL_FRAGMENTS)
    # Every handler the builder can produce is a catalog tool, and the
    # builder alone (no bindings) produces nothing.
    assert build_tool_handlers(None) == {}
    assert build_tool_handlers(McpToolBindings()) == {}
