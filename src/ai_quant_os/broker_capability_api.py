"""Protected declared broker implementation registry; no native execution enablement."""

from typing import Literal

from fastapi import Depends, Response
from pydantic import Field

from ai_quant_os.broker import BrokerRouter
from ai_quant_os.broker_capabilities import BROKER_PROFILES, ComplianceScope
from ai_quant_os.mt5_demo_observation import ObservationModel

Operation = Literal[
    "SIMULATED_ORDER_PLACE",
    "SIMULATED_ORDER_LOOKUP",
    "SIMULATED_FILLS_READ",
    "SIMULATED_FILL_REPORT",
    "QUOTE_READ",
    "ACCOUNT_READ",
    "POSITIONS_READ",
    "ACTIVE_ORDERS_READ",
    "DEALS_READ",
]


class BrokerCapability(ObservationModel):
    broker_id: Literal["PAPER", "EXNESS_MT5_DEMO", "EXNESS", "IBKR", "ZERODHA"]
    profile_version: Literal["0.1.0"] = "0.1.0"
    implementation: Literal["PAPER_SIMULATOR", "DEMO_OBSERVATION_LIBRARY", "DISABLED_SKELETON"]
    operations: tuple[Operation, ...] = Field(max_length=9)
    source_module: str = Field(pattern=r"^ai_quant_os\.[a-z0-9_]+$", max_length=96)
    bound_to_execution_router: bool = Field(strict=True)
    instruments: tuple[str, ...] = Field(max_length=4)
    eligible: bool = Field(strict=True)
    compliance_scope: ComplianceScope
    paper_admission_policy_allows: bool = Field(strict=True)
    live_execution_released: Literal[False] = False
    native_acceptance: Literal["NOT_APPLICABLE", "UNVERIFIED"]


class BrokerCapabilityRegistry(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    registry_kind: Literal["DECLARED_LOCAL_IMPLEMENTATIONS"] = "DECLARED_LOCAL_IMPLEMENTATIONS"
    brokers: tuple[BrokerCapability, ...] = Field(min_length=5, max_length=5)
    live_execution_released: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


def capability_registry(router: BrokerRouter) -> BrokerCapabilityRegistry:
    bound = {item["broker_id"]: item for item in router.snapshot()}
    rows = []
    for profile in BROKER_PROFILES:
        adapter = bound.get(profile.broker_id)
        eligibility = router.eligibility(profile.broker_id)
        paper_allowed = (
            profile.broker_id == "PAPER"
            and adapter is not None
            and adapter["enabled"] is True
            and adapter["connection_state"] == "CONNECTED"
            and eligibility.allows_paper_orders()
        )
        rows.append(
            BrokerCapability(
                broker_id=profile.broker_id,
                implementation=profile.implementation,
                operations=profile.operations,
                source_module=profile.module,
                bound_to_execution_router=adapter is not None,
                instruments=tuple(adapter["instruments"])
                if profile.broker_id == "PAPER" and adapter
                else (),
                eligible=eligibility.eligible,
                compliance_scope=eligibility.compliance_scope,
                paper_admission_policy_allows=paper_allowed,
                native_acceptance=profile.native_acceptance,
            )
        )
    return BrokerCapabilityRegistry(brokers=tuple(rows))


def install_broker_capability_routes(app, router: BrokerRouter, require_operator):
    @app.get(
        "/api/v1/brokers/capabilities",
        response_model=BrokerCapabilityRegistry,
        dependencies=[Depends(require_operator)],
    )
    def broker_capabilities(response: Response) -> BrokerCapabilityRegistry:
        response.headers["Cache-Control"] = "no-store"
        return capability_registry(router)
