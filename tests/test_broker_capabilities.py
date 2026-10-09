import importlib.util

import pytest

from ai_quant_os.broker import BrokerRouter, BrokerUnavailable, SimulatedBroker
from test_broker_router import NOW, command


def policy_module():
    assert importlib.util.find_spec("ai_quant_os.broker_capabilities") is not None
    from ai_quant_os import broker_capabilities

    return broker_capabilities


def test_enabled_external_adapter_cannot_bypass_release_boundary():
    external = SimulatedBroker("EXNESS", {"EURUSD": "EURUSDm"}, enabled=True)
    with pytest.raises(BrokerUnavailable, match="released"):
        BrokerRouter([external]).route(command(route="EXNESS"), now=NOW)
    assert external.orders == ()


def test_duplicate_broker_identity_cannot_silently_replace_adapter():
    adapters = [SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True) for _ in range(2)]
    with pytest.raises(ValueError, match="Duplicate"):
        BrokerRouter(adapters)


def test_paper_eligibility_flag_is_enforced_before_dispatch():
    module = policy_module()
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    denied = module.BrokerEligibility(eligible=False, compliance_scope="LOCAL_SIMULATION_ONLY")
    router = BrokerRouter([paper], eligibility={"PAPER": denied})
    with pytest.raises(BrokerUnavailable, match="eligibility"):
        router.route(command(), now=NOW)
    assert paper.orders == ()


@pytest.mark.parametrize("scope", ["UNVERIFIED", "DENIED"])
def test_unverified_or_denied_compliance_scope_cannot_place_order(scope):
    module = policy_module()
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    router = BrokerRouter(
        [paper],
        eligibility={"PAPER": module.BrokerEligibility(eligible=True, compliance_scope=scope)},
    )
    with pytest.raises(BrokerUnavailable, match="eligibility"):
        router.route(command(), now=NOW)
    assert paper.orders == ()


def test_local_simulation_flags_cannot_authorize_external_execution():
    module = policy_module()
    external = SimulatedBroker("IBKR", {"EURUSD": "EURUSD"}, enabled=True)
    local = module.BrokerEligibility(eligible=True, compliance_scope="LOCAL_SIMULATION_ONLY")
    with pytest.raises(BrokerUnavailable, match="released"):
        BrokerRouter([external], eligibility={"IBKR": local}).route(command(route="IBKR"), now=NOW)
    assert external.orders == ()


@pytest.mark.parametrize(
    "name,identity",
    [
        ("IBKRAdapterSkeleton", "IBKR"),
        ("ZerodhaAdapterSkeleton", "ZERODHA"),
        ("ExnessExecutionSkeleton", "EXNESS"),
    ],
)
def test_skeleton_never_fabricates_orders_or_fills(name, identity):
    assert importlib.util.find_spec("ai_quant_os.broker_skeletons") is not None
    from ai_quant_os import broker_skeletons

    adapter = getattr(broker_skeletons, name)()
    assert adapter.broker_id == identity and adapter.enabled is False and adapter.connected is False
    for operation, args in [
        (adapter.place, (command(route=identity),)),
        (adapter.lookup, (command(route=identity),)),
        (adapter.fills, (command(route=identity),)),
        (adapter.all_fills, ()),
    ]:
        with pytest.raises(BrokerUnavailable, match="not implemented"):
            operation(*args)


def test_eligibility_boolean_and_scope_are_strict():
    module = policy_module()
    for options in [{"eligible": 1}, {"eligible": "true"}, {"compliance_scope": "APPROVED_LIVE"}]:
        with pytest.raises(ValueError):
            module.BrokerEligibility(**options)


@pytest.mark.parametrize("field", ["enabled", "connected"])
def test_non_boolean_adapter_flags_cannot_claim_route_readiness(field):
    from ai_quant_os.broker_capability_api import capability_registry

    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    setattr(paper, field, 1)
    router = BrokerRouter([paper])
    snapshot = router.snapshot()[0]
    assert snapshot["enabled"] is (field != "enabled")
    assert snapshot["connection_state"] == ("DISCONNECTED" if field == "connected" else "CONNECTED")
    assert capability_registry(router).brokers[0].paper_admission_policy_allows is False
    with pytest.raises(BrokerUnavailable):
        router.route(command(), now=NOW)
    assert paper.orders == ()


def test_eligibility_denial_does_not_erase_existing_order_lookup():
    module = policy_module()
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    receipt = BrokerRouter([paper]).route(command(), now=NOW)
    policy = {"PAPER": module.BrokerEligibility()}
    router = BrokerRouter([paper], eligibility=policy)
    policy["PAPER"] = module.BrokerEligibility(True, "LOCAL_SIMULATION_ONLY")
    with pytest.raises(BrokerUnavailable, match="eligibility"):
        router.route(command(), now=NOW)
    assert router.lookup(command()) == receipt and len(paper.orders) == 1


@pytest.mark.parametrize("field", ["enabled", "connected"])
def test_direct_simulator_placement_rejects_new_order_when_disabled(field):
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    setattr(paper, field, False)
    with pytest.raises(BrokerUnavailable):
        paper.place(command())
    assert paper.orders == ()


def test_direct_simulator_replays_existing_ack_after_disable():
    paper = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    receipt = paper.place(command())
    paper.enabled = False
    paper.disconnect()
    assert paper.place(command()) == receipt and len(paper.orders) == 1
