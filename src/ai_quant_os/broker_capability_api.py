"""Protected declared broker implementation registry; no native execution enablement."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Literal

from fastapi import Depends, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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


class BrokerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    broker_id: Literal["PAPER", "EXNESS_MT5_DEMO", "EXNESS", "IBKR", "ZERODHA"]
    connection_state: Literal["CONNECTED", "DISCONNECTED", "UNKNOWN"]
    quote_age_ms: int | None = Field(default=None, ge=0, strict=True)
    order_latency_ms: int | None = Field(default=None, ge=0, strict=True)
    reject_rate: Decimal | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    reconciliation_state: Literal["RECONCILED", "MISMATCH", "UNASSESSED"] = "UNASSESSED"
    spread_state: Literal["WITHIN_LIMIT", "OUTSIDE_LIMIT", "UNASSESSED"] = "UNASSESSED"
    account_state: Literal["AVAILABLE", "UNAVAILABLE", "UNASSESSED"] = "UNASSESSED"
    health_score: Decimal | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    updated_at: AwareDatetime
    live_execution_released: Literal[False] = False

    @model_validator(mode="after")
    def withhold_unmeasured_score(self):
        if self.health_score is not None and (
            self.quote_age_ms is None
            or self.order_latency_ms is None
            or self.reject_rate is None
            or self.connection_state == "UNKNOWN"
            or "UNASSESSED" in {self.reconciliation_state, self.spread_state, self.account_state}
        ):
            raise ValueError("Health score requires complete measured evidence")
        return self


class BrokerHealthRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    brokers: tuple[BrokerHealth, ...] = Field(min_length=5, max_length=5)
    live_execution_released: Literal[False] = False

    @model_validator(mode="after")
    def exact_broker_catalog(self):
        if {row.broker_id for row in self.brokers} != {
            profile.broker_id for profile in BROKER_PROFILES
        }:
            raise ValueError("Broker health catalog mismatch")
        return self


def broker_health_registry(
    router: BrokerRouter,
    *,
    clock: Callable[[], datetime] | None = None,
) -> BrokerHealthRegistry:
    observed_at = (clock or (lambda: datetime.now(timezone.utc)))()
    adapters = {row["broker_id"]: row for row in router.snapshot()}
    return BrokerHealthRegistry(
        brokers=tuple(
            BrokerHealth(
                broker_id=profile.broker_id,
                connection_state=(
                    adapters[profile.broker_id]["connection_state"]
                    if profile.broker_id in adapters
                    else "UNKNOWN"
                ),
                updated_at=observed_at,
            )
            for profile in BROKER_PROFILES
        )
    )


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


def install_broker_capability_routes(
    app,
    router: BrokerRouter,
    require_operator,
    *,
    clock: Callable[[], datetime] | None = None,
):
    @app.get(
        "/api/v1/brokers/capabilities",
        response_model=BrokerCapabilityRegistry,
        dependencies=[Depends(require_operator)],
    )
    def broker_capabilities(response: Response) -> BrokerCapabilityRegistry:
        response.headers["Cache-Control"] = "no-store"
        return capability_registry(router)

    @app.get(
        "/api/v1/brokers/health",
        response_model=BrokerHealthRegistry,
        dependencies=[Depends(require_operator)],
    )
    def broker_health(response: Response) -> BrokerHealthRegistry:
        response.headers["Cache-Control"] = "no-store"
        return broker_health_registry(router, clock=clock)
