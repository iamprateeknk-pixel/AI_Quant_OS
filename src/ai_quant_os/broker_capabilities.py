"""Declared implementation profiles and local-simulation eligibility policy."""

from dataclasses import dataclass
from typing import Literal

ComplianceScope = Literal["LOCAL_SIMULATION_ONLY", "UNVERIFIED", "DENIED"]


@dataclass(frozen=True)
class BrokerEligibility:
    eligible: bool = False
    compliance_scope: ComplianceScope = "UNVERIFIED"

    def __post_init__(self):
        if type(self.eligible) is not bool or self.compliance_scope not in {
            "LOCAL_SIMULATION_ONLY",
            "UNVERIFIED",
            "DENIED",
        }:
            raise ValueError("Invalid broker eligibility declaration")

    def allows_paper_orders(self) -> bool:
        return self.eligible and self.compliance_scope == "LOCAL_SIMULATION_ONLY"


def default_eligibility(broker_id: str) -> BrokerEligibility:
    return (
        BrokerEligibility(True, "LOCAL_SIMULATION_ONLY")
        if broker_id == "PAPER"
        else BrokerEligibility()
    )


@dataclass(frozen=True)
class BrokerProfile:
    broker_id: str
    implementation: Literal["PAPER_SIMULATOR", "DEMO_OBSERVATION_LIBRARY", "DISABLED_SKELETON"]
    operations: tuple[str, ...]
    module: str
    native_acceptance: Literal["NOT_APPLICABLE", "UNVERIFIED"]


BROKER_PROFILES = (
    BrokerProfile(
        "PAPER",
        "PAPER_SIMULATOR",
        (
            "SIMULATED_ORDER_PLACE",
            "SIMULATED_ORDER_LOOKUP",
            "SIMULATED_FILLS_READ",
            "SIMULATED_FILL_REPORT",
        ),
        "ai_quant_os.broker",
        "NOT_APPLICABLE",
    ),
    BrokerProfile(
        "EXNESS_MT5_DEMO",
        "DEMO_OBSERVATION_LIBRARY",
        ("QUOTE_READ", "ACCOUNT_READ", "POSITIONS_READ", "ACTIVE_ORDERS_READ", "DEALS_READ"),
        "ai_quant_os.mt5_sync_service",
        "UNVERIFIED",
    ),
    BrokerProfile("EXNESS", "DISABLED_SKELETON", (), "ai_quant_os.broker_skeletons", "UNVERIFIED"),
    BrokerProfile("IBKR", "DISABLED_SKELETON", (), "ai_quant_os.broker_skeletons", "UNVERIFIED"),
    BrokerProfile("ZERODHA", "DISABLED_SKELETON", (), "ai_quant_os.broker_skeletons", "UNVERIFIED"),
)
