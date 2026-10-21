import asyncio
import hmac
import json
import os
import re
import sqlite3
from dataclasses import asdict
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.calibration import EvaluationBatch, build_calibration_report
from ai_quant_os.calibration_store import CalibrationStore
from ai_quant_os.agent_runs import AgentRun, verify_run_snapshot
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.graph_runtime import GraphRuntime
from ai_quant_os.incident_agent import incident_implementation
from ai_quant_os.incident_sources import (
    INCIDENT_INPUT_BYTES,
    INCIDENT_CONTEXT_BYTES,
    incident_sources,
)
from ai_quant_os.incident_proposal_store import IncidentProposalStore
from ai_quant_os.incident_proposal_api import IncidentPropose, install_incident_proposal_routes
from ai_quant_os.supervisor_agent import supervisor_implementation
from ai_quant_os.supervisor_drift import supervisor_drift_implementation
from ai_quant_os.task_supervisor import (
    TaskSupervisionInput,
    task_supervisor_implementation,
)
from ai_quant_os.task_supervisor_incident import (
    IncidentSupervisionInput,
    task_supervisor_incident_implementation,
)
from ai_quant_os.task_supervisor_research import (
    task_supervisor_research_implementation,
    task_supervisor_research_implementation_v3,
    task_supervisor_research_implementation_v4,
    task_supervisor_research_implementation_v5,
)
from ai_quant_os.task_supervisor_research_api import install_research_supervisor_routes
from ai_quant_os.graph_trace import GraphTrace
from ai_quant_os.graph_archive import GraphRunPage, GraphArchiveChanged
from ai_quant_os.supervisor_dispatch import SupervisorDispatch
from ai_quant_os.supervisor_sources import (
    SUPERVISOR_INPUT_BYTES,
    SUPERVISOR_CONTEXT_BYTES,
    supervisor_sources,
)
from ai_quant_os.regime_agent import regime_implementation
from ai_quant_os.currency_strength_agent import currency_strength_implementation
from ai_quant_os.liquidity_agent import liquidity_implementation
from ai_quant_os.event_agent import event_implementation
from ai_quant_os.event_feed_store import EventFeedStore
from ai_quant_os.event_feed_api import install_event_feed_routes
from ai_quant_os.event_sources import EVENT_INPUT_BYTES, EVENT_CONTEXT_BYTES, event_sources
from ai_quant_os.macro_agent import macro_implementation
from ai_quant_os.macro_sources import (
    MACRO_INPUT_BYTES,
    MACRO_CONTEXT_BYTES,
    latest_rate_capture,
    macro_sources,
)
from ai_quant_os.liquidity_sources import (
    LIQUIDITY_INPUT_BYTES,
    LIQUIDITY_CONTEXT_BYTES,
    liquidity_sources,
    DisplayedDepth,
)
from ai_quant_os.currency_strength_sources import (
    STRENGTH_INPUT_BYTES,
    STRENGTH_CONTEXT_BYTES,
    currency_strength_sources,
)
from ai_quant_os.integrity_agent import integrity_implementation, integrity_sources
from ai_quant_os.ingress_health import IngressHealth
from ai_quant_os.agent_context import CompiledContext, ContextCompiler, require_context_for_agent
from ai_quant_os.paper_context_sources import paper_context_sources
from ai_quant_os.context_checkpoints import ContextCheckpoint
from ai_quant_os.jev_adapter import JevClassifier, jev_from_environment
from ai_quant_os.openai_reviewer import OpenAIReviewer, reviewer_from_environment
from ai_quant_os.provider_spend_api import install_spending_routes
from ai_quant_os.shared_spend_api import SpendingSource, install_shared_spending_routes
from ai_quant_os.paper_telemetry import (
    PaperTelemetry,
    PaperTraceMiddleware,
    telemetry_from_environment,
)
from ai_quant_os.tracing_status_api import install_tracing_status_routes
from ai_quant_os.paper_metrics_api import install_paper_metrics_routes
from ai_quant_os.agent_trace_binding import make_agent_trace_binding
from ai_quant_os.agent_trace_binding_api import install_agent_trace_binding_routes
from ai_quant_os.collector_trace_source import CollectorTraceSource
from ai_quant_os.collector_trace_api import install_collector_trace_routes
from ai_quant_os.monitoring_alerts import MonitoringAlertSource, install_monitoring_alert_routes
from ai_quant_os.monitoring_journal import MonitoringJournal
from ai_quant_os.monitoring_journal_api import install_monitoring_journal_routes
from ai_quant_os.contracts import TradeIntent
from ai_quant_os.currency_portfolio import FxRate, summarize_currency_portfolio
from ai_quant_os.currency_risk import CurrencyRiskContext, CurrencyRiskPolicy
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.paper_market_ingress import require_paper_prices
from ai_quant_os.broker import BrokerRouter, SimulatedBroker
from ai_quant_os.execution import PaperExecutionGateway, PaperOrder
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.world_state import WorldState, WorldStateEngine, fingerprint
from ai_quant_os.world_state_store import WorldStateStore
from ai_quant_os.decision_gateway import DecisionGateway, FastClassifier, require_current_decision
from ai_quant_os.deep_review import DeepReviewer, DeepReviewDispatcher, require_current_review
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.operator_session_store import OperatorSessionStore
from ai_quant_os.operator_session_api import OperatorAuthorization
from ai_quant_os.operator_control_api import install_operator_control_routes
from ai_quant_os.broker_route_control_api import install_broker_route_control_routes
from ai_quant_os.portfolio import PaperFill, summarize_position
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
from ai_quant_os.research_api import install_research_routes
from ai_quant_os.provider_call_admission import (
    AdmittedClassifier,
    AdmittedReviewer,
    ProviderCallAdmission,
    ProviderOwner,
    provider_owner,
)
from ai_quant_os.agent_call_policy import AgentCallPolicy
from ai_quant_os.agent_call_api import call_budget_configuration, install_agent_call_routes
from ai_quant_os.agent_call_budgets import AgentCallBudget
from ai_quant_os.execution_drift import DriftPolicy
from ai_quant_os.drift_sources import drift_configuration, drift_sources
from ai_quant_os.drift_agent import drift_implementation
from ai_quant_os.evolution_api import install_evolution_routes
from ai_quant_os.research_data_api import install_research_data_routes
from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
from ai_quant_os.research_hypothesis_agent import (
    hypothesis_implementation,
    hypothesis_implementation_v3,
)
from ai_quant_os.research_hypothesis_api import install_hypothesis_routes
from ai_quant_os.research_hypothesis_sources import (
    HYPOTHESIS_INPUT_BYTES,
    HYPOTHESIS_CONTEXT_BYTES,
    hypothesis_sources,
)
from ai_quant_os.research_hypothesis_contracts import hypothesis_recipe, hypothesis_recipe_v3
from ai_quant_os.research_novelty import ResearchNoveltyReservations
from ai_quant_os.graph_ledger import GraphLedger
from ai_quant_os.strategy_build_store import StrategyBuildStore
from ai_quant_os.strategy_builder_agent import builder_implementation
from ai_quant_os.strategy_builder_api import install_builder_routes
from ai_quant_os.validation_package_api import install_validation_package_routes
from ai_quant_os.validation_package_service import ValidationPreparation
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.strategy_builder_sources import builder_sources
from ai_quant_os.strategy_builder_service import CandidateCreate
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.validation_scientist_agent import validation_implementation
from ai_quant_os.validation_scientist_sources import (
    summary_for_context,
    validation_sources_from_summary,
    ValidationContextSnapshot,
)
from ai_quant_os.validation_scientist_service import IsolatedValidationRun
from ai_quant_os.holdout_validator_rpc import (
    HoldoutContextSnapshot,
    HoldoutRequest,
    installed_windows_holdout_client,
)
from ai_quant_os.validation_scientist_api import install_validation_scientist_routes
from ai_quant_os.adversarial_agent import (
    AdversarialContextSnapshot,
    adversarial_implementation,
    adversarial_sources_from_snapshot,
)
from ai_quant_os.adversarial_api import install_adversarial_routes
from ai_quant_os.adversarial_service import IsolatedStressRun as AdversarialStressRun
from ai_quant_os.strategy_selector_agent import (
    EligibleStrategySetV3,
    selector_implementation,
    selector_implementation_v3,
)
from ai_quant_os.strategy_selector_service import StrategyRegistryRead
from ai_quant_os.strategy_selector_sources import (
    RegistrySnapshot,
    selector_sources,
    selector_sources_v3,
)
from ai_quant_os.meta_allocator_agent import (
    allocator_implementation,
    allocator_implementation_v3,
)
from ai_quant_os.meta_allocator_sources import (
    AllocationInputs,
    allocator_sources,
    allocator_sources_v3,
)
from ai_quant_os.counterfactual_agent import counterfactual_implementation
from ai_quant_os.counterfactual_sources import counterfactual_sources
from ai_quant_os.health_repair_agent import repair_implementation
from ai_quant_os.health_repair_sources import RepairInputs, RepairTrace, repair_sources
from ai_quant_os.evolution_director_agent import evolution_implementation
from ai_quant_os.evolution_director_sources import evolution_sources
from ai_quant_os.health_watchdog import owned_health_call as owned_research_call
from ai_quant_os.daily_api import install_daily_routes
from ai_quant_os.paper_attribution import build_strategy_contributions
from ai_quant_os.execution_attribution import build_execution_contributions


def _available_candidate_store(workspace):
    if workspace is None:
        return None
    path = workspace / "candidates.sqlite3"
    if not path.is_file():
        return None
    from ai_quant_os.research_candidate_store import CandidateStore

    return CandidateStore(path)


def _candidate_registry_snapshot(workspace):
    store = _available_candidate_store(workspace)
    return RegistrySnapshot(
        available=store is not None,
        candidates=() if store is None else store.list(),
    )


def _available_shadow_store(workspace):
    if workspace is None:
        return None
    path = workspace / "shadow.sqlite3"
    if not path.is_file():
        return None
    from ai_quant_os.shadow_store import ShadowStore

    return ShadowStore(path)


def _available_counterfactual_report(workspace, replay_id):
    if workspace is None:
        return None
    path = workspace / "counterfactual.sqlite3"
    if not path.is_file():
        return None
    from ai_quant_os.counterfactual_store import CounterfactualStore

    return CounterfactualStore.read_existing(path, replay_id)


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    world_state_version: str


class PaperL1QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: QuoteInput
    bid_size: Decimal = Field(ge=0, le=Decimal("1e12"), allow_inf_nan=False)
    ask_size: Decimal = Field(ge=0, le=Decimal("1e12"), allow_inf_nan=False)


class WorldStateBarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class WorldStateBarsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bars: list[WorldStateBarInput] = Field(min_length=1, max_length=1000)


class ClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=1, max_length=32)
    task_id: str = Field(min_length=1, max_length=128)


class AgentContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(min_length=1, max_length=32)
    task_id: str = Field(min_length=1, max_length=128)
    upstream_run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")


class AgentGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_ref: str = Field(min_length=1, max_length=128)


class WorldStatePublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = Field(default="1.0", exclude=True)
    intent_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    desired_exposure: Decimal
    risk_budget: Decimal
    max_spread: Decimal
    max_slippage: Decimal
    valid_until: datetime
    world_state_version: str
    evidence_refs: tuple[str, ...]
    created_at: datetime
    candidate_assignment_id: str | None = Field(
        default=None,
        exclude=True,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )


class CandidatePaperAssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    strategy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    instrument_id: str = Field(min_length=1, max_length=32)
    max_exposure: Decimal = Field(gt=0, max_digits=30, decimal_places=12)
    expires_at: datetime


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


class FillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fill_id: str
    order_id: str
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee: Decimal = Field(ge=0)
    observed_at: datetime


def _order_view(order: PaperOrder) -> dict[str, str]:
    view = {
        "order_id": order.order_id,
        "intent_id": order.intent_id,
        "risk_id": order.risk_id,
        "status": order.status,
        "broker_route": "PAPER",
        "instrument_id": order.command.instrument_id,
    }
    if order.broker_order_id is not None:
        view["broker_order_id"] = order.broker_order_id
    return view


def _fill_view(fill: PaperFill) -> dict[str, str]:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "instrument_id": fill.instrument_id,
        "side": fill.side,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee": str(fill.fee),
        "observed_at": fill.observed_at.isoformat(),
        "source": fill.source,
    }


def create_app(
    *,
    operator_token: str | None = None,
    recovery_token: str | None = None,
    clock: Callable[[], datetime] | None = None,
    paper_db_path: Path | None = None,
    fast_classifier: FastClassifier | None = None,
    deep_reviewer: DeepReviewer | None = None,
    research_workspace: Path | None = None,
    evolution_workspace: Path | None = None,
    agent_call_policy: AgentCallPolicy | None = None,
    classification_owner: ProviderOwner | None = None,
    review_owner: ProviderOwner | None = None,
    drift_policy: DriftPolicy | None = None,
    daily_evidence_enabled: bool | None = None,
    shadow_scheduler_enabled: bool | None = None,
    evolution_scheduler_enabled: bool | None = None,
    temporal_recovery_enabled: bool | None = None,
    temporal_recovery_target: str | None = None,
    temporal_repair_enabled: bool | None = None,
    temporal_repair_repository: Path | None = None,
    temporal_repair_trust_store: Path | None = None,
    temporal_repair_trust_root_public_key: bytes | None = None,
    health_watchdog_enabled: bool | None = None,
    demo_sync_state_dir: Path | None = None,
    telemetry: PaperTelemetry | None = None,
    collector_trace_file: Path | None = None,
    monitoring_alert_source: MonitoringAlertSource | None = None,
    holdout_rpc_client=None,
) -> FastAPI:
    configured_token = (
        operator_token if operator_token is not None else os.getenv("AI_QUANT_OPERATOR_TOKEN")
    )
    configured_recovery_token = (
        recovery_token if recovery_token is not None else os.getenv("AI_QUANT_RECOVERY_TOKEN")
    )
    if (
        configured_token
        and configured_recovery_token
        and hmac.compare_digest(configured_token, configured_recovery_token)
    ):
        raise ValueError("Recovery token must differ from operator token")
    current_time = clock or (lambda: datetime.now(timezone.utc))
    configured_research_workspace = research_workspace or (
        Path(os.environ["AI_QUANT_RESEARCH_WORKSPACE"])
        if os.getenv("AI_QUANT_RESEARCH_WORKSPACE")
        else None
    )
    configured_evolution_workspace = evolution_workspace or (
        Path(os.environ["AI_QUANT_EVOLUTION_WORKSPACE"])
        if os.getenv("AI_QUANT_EVOLUTION_WORKSPACE")
        else None
    )
    configured_demo_sync_state_dir = demo_sync_state_dir or (
        Path(os.environ["AI_QUANT_DEMO_SYNC_STATE"])
        if os.getenv("AI_QUANT_DEMO_SYNC_STATE")
        else None
    )
    limits = RiskLimits(
        max_order_exposure=Decimal("1000"),
        max_portfolio_exposure=Decimal("1500"),
        max_quote_age_seconds=5,
    )
    currency_policy = CurrencyRiskPolicy(
        "USD", Decimal("2000"), Decimal("4000"), 5, PAPER_INSTRUMENTS
    )
    configured_classifier = (
        fast_classifier if fast_classifier is not None else jev_from_environment()
    )
    configured_reviewer = (
        deep_reviewer if deep_reviewer is not None else reviewer_from_environment()
    )
    world_state = WorldStateEngine(PAPER_INSTRUMENTS, max_quote_age_seconds=5)
    instrument_catalog = {item.instrument_id: item for item in PAPER_INSTRUMENTS}
    risk = RiskKernel(limits, currency_policy=currency_policy)
    configured_path = paper_db_path or (
        Path(os.environ["AI_QUANT_PAPER_DB"]) if os.getenv("AI_QUANT_PAPER_DB") else None
    )
    configured_call_policy = call_budget_configuration(
        agent_call_policy, configured_path, configured_token
    )
    configured_drift_policy = drift_configuration(drift_policy)
    call_budget = AgentCallBudget(
        configured_path.with_name(configured_path.stem + ".agent-calls.sqlite3")
        if configured_path
        else None,
        policy=configured_call_policy,
        clock=current_time,
    )
    daily_setting = os.getenv("AI_QUANT_DAILY_EVIDENCE_ENABLED", "0")
    if daily_evidence_enabled is None and daily_setting not in ("0", "1"):
        raise ValueError("AI_QUANT_DAILY_EVIDENCE_ENABLED must be 0 or 1")
    enabled_daily = (
        daily_setting == "1" if daily_evidence_enabled is None else daily_evidence_enabled
    )
    if type(enabled_daily) is not bool or (
        enabled_daily and (configured_path is None or not configured_token)
    ):
        raise ValueError("Daily scheduler requires persistent paper storage and an operator token")
    shadow_setting = os.getenv("AI_QUANT_SHADOW_SCHEDULER_ENABLED", "0")
    if shadow_scheduler_enabled is None and shadow_setting not in ("0", "1"):
        raise ValueError("AI_QUANT_SHADOW_SCHEDULER_ENABLED must be 0 or 1")
    enabled_shadow_scheduler = (
        shadow_setting == "1" if shadow_scheduler_enabled is None else shadow_scheduler_enabled
    )
    if type(enabled_shadow_scheduler) is not bool or (
        enabled_shadow_scheduler
        and (
            configured_path is None or not configured_token or configured_research_workspace is None
        )
    ):
        raise ValueError(
            "Shadow scheduler requires persistent paper storage, an operator token, "
            "and a research workspace"
        )
    evolution_scheduler_setting = os.getenv("AI_QUANT_EVOLUTION_SCHEDULER_ENABLED", "0")
    if evolution_scheduler_enabled is None and evolution_scheduler_setting not in ("0", "1"):
        raise ValueError("AI_QUANT_EVOLUTION_SCHEDULER_ENABLED must be 0 or 1")
    enabled_evolution_scheduler = (
        evolution_scheduler_setting == "1"
        if evolution_scheduler_enabled is None
        else evolution_scheduler_enabled
    )
    if type(enabled_evolution_scheduler) is not bool or (
        enabled_evolution_scheduler
        and (configured_evolution_workspace is None or not configured_token)
    ):
        raise ValueError(
            "Evolution scheduler requires a persistent evolution workspace and operator token"
        )
    if enabled_shadow_scheduler and configured_research_workspace is not None:
        required_shadow_inputs = (
            configured_research_workspace / "shadow.sqlite3",
            configured_research_workspace / "candidates.sqlite3",
            configured_research_workspace / "strategies",
        )
        if not (
            required_shadow_inputs[0].is_file()
            and required_shadow_inputs[1].is_file()
            and required_shadow_inputs[2].is_dir()
        ):
            raise ValueError("Shadow scheduler requires existing shadow research stores")
    evolution_scheduler = None
    if enabled_evolution_scheduler:
        from ai_quant_os.evolution_scheduler import EvolutionScheduler

        evolution_scheduler = EvolutionScheduler(
            configured_evolution_workspace / "evaluation.sqlite3",
            current_time,
        )
    from ai_quant_os.recovery_api import install_recovery_routes, recovery_configuration

    enabled_recovery, recovery_target = recovery_configuration(
        temporal_recovery_enabled, temporal_recovery_target, configured_path, configured_token
    )
    from ai_quant_os.health_api import health_configuration, install_health_routes

    enabled_health = health_configuration(
        health_watchdog_enabled, configured_path, configured_token
    )
    from ai_quant_os.repair_temporal_api import (
        install_repair_temporal_routes,
        repair_configuration,
    )

    (
        enabled_temporal_repair,
        configured_repair_repository,
        repair_verifier_trust_store,
    ) = repair_configuration(
        temporal_repair_enabled,
        temporal_repair_repository,
        paper_path=configured_path,
        operator_token=configured_token,
        health_enabled=enabled_health,
        recovery_enabled=enabled_recovery,
        trust_store_path=temporal_repair_trust_store,
        trust_root_public_key=temporal_repair_trust_root_public_key,
    )
    state_store = PaperStateStore(configured_path)
    calibration_store = CalibrationStore(configured_path)
    world_state_store = WorldStateStore(
        configured_path,
        connection=state_store._connection,
        lock=state_store._lock,
    )
    event_feed_store = EventFeedStore(
        configured_path.with_name(configured_path.stem + ".events.sqlite3")
        if configured_path
        else None
    )
    incident_proposal_store = IncidentProposalStore(
        configured_path.with_name(configured_path.stem + ".incident-proposals.sqlite3")
        if configured_path
        else None
    )
    monitoring_journal = MonitoringJournal(
        configured_path.with_name(configured_path.stem + ".monitoring.sqlite3")
        if configured_path
        else None
    )
    agent_registry = load_agent_catalog()
    if configured_path is not None:
        from ai_quant_os.agent_lifecycle_store import AgentLifecycleStore

        agent_registry.bind_lifecycle_store(
            AgentLifecycleStore(
                configured_path.with_name(configured_path.stem + ".agent-lifecycle.sqlite3")
            )
        )
    classification_admission = ProviderCallAdmission(
        agent_registry,
        call_budget,
        provider_owner(classification_owner, "AI_QUANT_CLASSIFICATION_OWNER"),
        clock=current_time,
    )
    review_admission = ProviderCallAdmission(
        agent_registry,
        call_budget,
        provider_owner(review_owner, "AI_QUANT_REVIEW_OWNER"),
        clock=current_time,
    )
    decisions = DecisionGateway(
        primary=AdmittedClassifier(
            configured_classifier,
            classification_admission,
            span=lambda: configured_telemetry.span("paper.provider.classify"),
        )
        if configured_classifier is not None
        else None,
        clock=current_time,
    )
    deep_reviews = DeepReviewDispatcher(
        reviewer=AdmittedReviewer(
            configured_reviewer,
            review_admission,
            span=lambda: configured_telemetry.span("paper.provider.review"),
        )
        if configured_reviewer is not None
        else None,
        clock=current_time,
    )
    research_data_catalog = ResearchEvidenceCatalog(
        configured_path.with_name(configured_path.stem + ".research-data.sqlite3")
        if configured_path
        else None,
        training_only=True,
    )
    research_novelty_reservations = ResearchNoveltyReservations(
        configured_path.with_name(configured_path.stem + ".research-novelty.sqlite3")
        if configured_path
        else None
    )
    strategy_build_store = StrategyBuildStore(
        configured_path.with_name(configured_path.stem + ".strategy-builds.sqlite3")
        if configured_path
        else None
    )
    validation_package_store = ValidationPackageStore(
        configured_path.with_name(configured_path.stem + ".validation-packages.sqlite3")
        if configured_path
        else None
    )
    ingress_health = IngressHealth()
    candidate_validation_store = CandidateValidationStore(
        configured_path.with_name(configured_path.stem + ".candidate-validations.sqlite3")
        if configured_path
        else None
    )
    validator_client = holdout_rpc_client or installed_windows_holdout_client()
    risk.set_safety_state(state_store.safety_state())
    router = BrokerRouter(
        [
            SimulatedBroker(
                "PAPER",
                {identifier: identifier for identifier in instrument_catalog},
                enabled=True,
                store_path=configured_path,
            )
        ]
    )
    try:
        router.set_enabled("PAPER", state_store.broker_route_enabled("PAPER", default_enabled=True))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        raise ValueError("Persistent PAPER broker route state could not be verified") from error
    execution = PaperExecutionGateway(
        risk,
        store_path=configured_path,
        router=router,
        on_uncertain_order=state_store.record_order_uncertainty,
        clock=current_time,
    )
    quotes: dict[str, MarketSnapshot] = {}
    displayed_depth: dict[str, DisplayedDepth] = {}
    control_lock = Lock()
    candidate_paper_store = None

    def paper_candidate_store():
        nonlocal candidate_paper_store
        if candidate_paper_store is None:
            candidate_paper_store = _available_candidate_store(configured_research_workspace)
        return candidate_paper_store

    def candidate_admission_guard():
        """Serialize PAPER candidate admission with lifecycle changes in this API."""
        with control_lock:
            yield

    health_worker = None
    if configured_path is not None:
        health_path = configured_path.with_name(configured_path.stem + ".health.sqlite3")
        if enabled_health or health_path.exists():
            from ai_quant_os.health_store import HealthStore
            from ai_quant_os.health_watchdog import HealthWatchdog, owned_health_call

            health_worker = HealthWatchdog(
                HealthStore(health_path),
                state_store,
                risk,
                execution,
                agent_registry,
                lambda cursor: (
                    app.state.graph_runtime.health_receipts(cursor)
                    if app.state.graph_runtime is not None
                    else ()
                ),
                lambda: tuple(quotes.copy().values()),
                current_time,
                control_lock=control_lock,
            )
            # Disabling monitoring cannot clear persisted safety/quarantine evidence.
            health_worker.restore()
    daily_worker = None
    if enabled_daily:
        from ai_quant_os.daily_scheduler import DailyScheduler
        from ai_quant_os.evidence_store import EvidenceStore

        evidence_path = configured_path.with_name(configured_path.stem + ".evidence.sqlite3")
        daily_worker = DailyScheduler(
            EvidenceStore(evidence_path),
            lambda: execution.attribution_ledger,
            lambda: tuple(quotes.copy().values()),
            PAPER_INSTRUMENTS,
            current_time,
            lambda: state_store.audit_integrity().valid,
            execution_ledger=lambda: execution.execution_evidence_ledger,
        )
    shadow_worker = None
    if enabled_shadow_scheduler:
        from ai_quant_os.research_candidate_store import CandidateStore
        from ai_quant_os.shadow_scheduler import ShadowScheduler
        from ai_quant_os.shadow_store import ShadowStore
        from ai_quant_os.strategy_registry import StrategyRegistry

        shadow_worker = ShadowScheduler(
            ShadowStore(configured_research_workspace / "shadow.sqlite3"),
            world_state_store,
            CandidateStore(configured_research_workspace / "candidates.sqlite3"),
            StrategyRegistry(configured_research_workspace / "strategies"),
            current_time,
            lambda: state_store.audit_integrity().valid,
        )

    @asynccontextmanager
    async def service_lifespan(application: FastAPI):
        application.state.graph_runtime = None
        if configured_path is None:
            try:
                if evolution_scheduler is not None:
                    from ai_quant_os.evolution_scheduler import run_evolution_scheduler

                    application.state.evolution_task = asyncio.create_task(
                        run_evolution_scheduler(evolution_scheduler)
                    )
                yield
            finally:
                if application.state.evolution_task is not None:
                    application.state.evolution_task.cancel()
                    try:
                        await application.state.evolution_task
                    except asyncio.CancelledError:
                        pass
                    application.state.evolution_task = None
            return
        graph_path = configured_path.with_name(configured_path.stem + ".agents.sqlite3")
        validation_run_tool = IsolatedValidationRun(
            validator_client,
            agent_registry,
            context=archived_agent_context,
            clock=current_time,
        )
        stress_run_tool = AdversarialStressRun(
            validator_client,
            agent_registry,
            context=archived_agent_context,
            clock=current_time,
        )
        application.state.validation_run_tool = validation_run_tool
        application.state.stress_run_tool = stress_run_tool
        async with (
            GraphRuntime.open(
                graph_path,
                registry=agent_registry,
                implementations={
                    ("A01", "0.2.0"): supervisor_implementation(agent_registry),
                    ("A01", "0.3.0"): supervisor_drift_implementation(agent_registry),
                    ("A01", "0.4.0"): task_supervisor_implementation(agent_registry),
                    ("A01", "0.5.0"): task_supervisor_incident_implementation(agent_registry),
                    ("A01", "0.6.0"): task_supervisor_research_implementation(agent_registry),
                    ("A01", "0.7.0"): task_supervisor_research_implementation_v3(agent_registry),
                    ("A01", "0.8.0"): task_supervisor_research_implementation_v4(agent_registry),
                    ("A01", "0.9.0"): task_supervisor_research_implementation_v5(agent_registry),
                    ("A02", "0.2.0"): regime_implementation(agent_registry),
                    ("A03", "0.2.0"): macro_implementation(agent_registry),
                    ("A04", "0.2.0"): currency_strength_implementation(agent_registry),
                    ("A05", "0.2.0"): liquidity_implementation(agent_registry),
                    ("A06", "0.2.0"): event_implementation(agent_registry),
                    ("A07", "0.2.0"): integrity_implementation(agent_registry),
                    ("A08", "0.2.0"): hypothesis_implementation(agent_registry),
                    ("A08", "0.3.0"): hypothesis_implementation_v3(agent_registry),
                    ("A09", "0.2.0"): builder_implementation(agent_registry),
                    ("A10", "0.2.0"): validation_implementation(agent_registry),
                    ("A11", "0.2.0"): adversarial_implementation(agent_registry),
                    ("A12", "0.2.0"): selector_implementation(agent_registry),
                    ("A12", "0.3.0"): selector_implementation_v3(agent_registry),
                    ("A13", "0.2.0"): allocator_implementation(agent_registry),
                    ("A13", "0.3.0"): allocator_implementation_v3(agent_registry),
                    ("A14", "0.2.0"): drift_implementation(agent_registry),
                    ("A15", "0.2.0"): counterfactual_implementation(agent_registry),
                    ("A16", "0.2.0"): repair_implementation(agent_registry),
                    ("A16", "0.3.0"): repair_implementation(agent_registry, "0.3.0"),
                    ("A16", "0.4.0"): repair_implementation(agent_registry, "0.4.0"),
                    ("A17", "0.2.0"): evolution_implementation(agent_registry),
                    ("A18", "0.2.0"): incident_implementation(agent_registry),
                },
                tools=AgentToolBoundary(
                    agent_registry,
                    {
                        "VALIDATION_RUN": validation_run_tool,
                        "STRESS_RUN": stress_run_tool,
                        "STRATEGY_REGISTRY_READ": StrategyRegistryRead(
                            lambda: _available_candidate_store(configured_research_workspace),
                            agent_registry,
                            context=archived_agent_context,
                            clock=current_time,
                            shadow_store=lambda: _available_shadow_store(
                                configured_research_workspace
                            ),
                        ),
                        "CANDIDATE_CREATE": CandidateCreate(
                            strategy_build_store,
                            agent_registry,
                            context=archived_agent_context,
                            clock=current_time,
                        ),
                        "TASK_ORCHESTRATE": SupervisorDispatch(
                            agent_registry,
                            runtime=require_graph_runtime,
                            context=archived_agent_context,
                            compile_child=compile_supervisor_child,
                            compile_child_versioned=compile_supervisor_child_versioned,
                            compile_child_parent_aware=compile_supervisor_child_parent_aware,
                            admission=supervisor_admission,
                            observe=observe_supervisor_child,
                        ),
                        "INCIDENT_PROPOSE": IncidentPropose(
                            incident_proposal_store,
                            agent_registry,
                            context=archived_agent_context,
                            clock=current_time,
                            risk_states=lambda: (risk.safety_state, state_store.safety_state()),
                        ),
                    },
                    call_budget=call_budget,
                ),
                clock=current_time,
            ) as runtime,
            AsyncExitStack() as services,
        ):
            application.state.graph_runtime = runtime
            try:
                if enabled_health:
                    from ai_quant_os.health_watchdog import run_health_watchdog

                    # Cover the bounded graph ledger completely before serving requests.
                    while True:
                        await owned_health_call(health_worker.step)
                        if not health_worker.pending_graph_scan:
                            break
                if enabled_recovery:
                    from ai_quant_os.recovery_runtime import RecoveryRuntime

                    repair_activities = None
                    if enabled_temporal_repair:
                        from ai_quant_os.repair_workspace import RepairWorkspace
                        from ai_quant_os.temporal_repair import RepairRecoveryActivities

                        if health_worker is None:
                            raise ValueError(
                                "Temporal repair recovery requires the health watchdog"
                            )
                        repair_root = configured_path.with_name(configured_path.stem + ".repairs")
                        repair_workspace = RepairWorkspace(
                            configured_repair_repository,
                            repair_root,
                            health_worker.store,
                            agent_registry,
                        )
                        repair_activities = RepairRecoveryActivities(
                            health_worker,
                            repair_workspace,
                            jobs_root=repair_root / "jobs",
                            verifier_trust_store=repair_verifier_trust_store,
                        )

                    application.state.recovery_runtime = await services.enter_async_context(
                        RecoveryRuntime.open(
                            recovery_target,
                            configured_path,
                            execution,
                            risk,
                            state_store,
                            control_lock,
                            repair_activities=repair_activities,
                        )
                    )
                if daily_worker is not None:
                    from ai_quant_os.daily_scheduler import run_daily_scheduler

                    application.state.daily_task = asyncio.create_task(
                        run_daily_scheduler(daily_worker)
                    )
                if evolution_scheduler is not None:
                    from ai_quant_os.evolution_scheduler import run_evolution_scheduler

                    application.state.evolution_task = asyncio.create_task(
                        run_evolution_scheduler(evolution_scheduler)
                    )
                if shadow_worker is not None:
                    from ai_quant_os.shadow_scheduler import run_shadow_scheduler

                    application.state.shadow_task = asyncio.create_task(
                        run_shadow_scheduler(shadow_worker)
                    )
                if enabled_health:
                    application.state.health_task = asyncio.create_task(
                        run_health_watchdog(health_worker)
                    )
                yield
            finally:
                if application.state.health_task is not None:
                    application.state.health_task.cancel()
                    try:
                        await application.state.health_task
                    except asyncio.CancelledError:
                        pass
                    application.state.health_task = None
                if application.state.daily_task is not None:
                    application.state.daily_task.cancel()
                    try:
                        await application.state.daily_task
                    except asyncio.CancelledError:
                        pass
                    application.state.daily_task = None
                if application.state.shadow_task is not None:
                    application.state.shadow_task.cancel()
                    try:
                        await application.state.shadow_task
                    except asyncio.CancelledError:
                        pass
                    application.state.shadow_task = None
                if application.state.evolution_task is not None:
                    application.state.evolution_task.cancel()
                    try:
                        await application.state.evolution_task
                    except asyncio.CancelledError:
                        pass
                    application.state.evolution_task = None
                application.state.graph_runtime = None
                application.state.recovery_runtime = None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            if configured_path is None:
                async with service_lifespan(application):
                    yield
            else:
                from ai_quant_os.root_ownership import kernel_root_lock

                with kernel_root_lock(
                    configured_path.expanduser().resolve().parent,
                    lock_name=".paper-api-owner.lock",
                ):
                    async with service_lifespan(application):
                        yield
        finally:
            if application.state.evolution_task is not None:
                application.state.evolution_task.cancel()
                try:
                    await application.state.evolution_task
                except asyncio.CancelledError:
                    pass
                application.state.evolution_task = None
            if telemetry is None:
                await asyncio.to_thread(configured_telemetry.close)
            research_novelty_reservations.close()

    configured_telemetry = telemetry if telemetry is not None else telemetry_from_environment()
    app = FastAPI(title="AI Quant OS Paper Control API", version="0.1.0", lifespan=lifespan)
    app.state.holdout_rpc_client = validator_client

    @app.exception_handler(RequestValidationError)
    async def count_schema_rejected_ingress(request, exc):
        if request.method == "POST":
            if request.url.path == "/api/v1/paper/quotes":
                ingress_health.note("SCHEMA_REJECTED_QUOTE")
            elif request.url.path == "/api/v1/paper/world-state/bars":
                ingress_health.note("SCHEMA_REJECTED_BAR")
        return await request_validation_exception_handler(request, exc)

    app.add_middleware(PaperTraceMiddleware, telemetry=configured_telemetry)
    app.state.paper_telemetry = configured_telemetry
    app.state.graph_runtime = None
    app.state.daily_task = None
    app.state.shadow_task = None
    app.state.evolution_task = None
    app.state.evolution_scheduler = evolution_scheduler
    app.state.recovery_runtime = None
    app.state.temporal_repair_enabled = enabled_temporal_repair
    app.state.repair_verifier_trust_store = repair_verifier_trust_store
    app.state.health_task = None

    def currency_context(primary_quote: MarketSnapshot | None = None) -> CurrencyRiskContext:
        instruments = currency_policy.instruments
        rates: list[FxRate] = []
        for item in instruments:
            quote = (
                primary_quote
                if primary_quote is not None and primary_quote.instrument_id == item.instrument_id
                else quotes.get(item.instrument_id)
            )
            if quote is not None:
                rates.append(
                    FxRate(
                        item.base_currency,
                        item.quote_currency,
                        quote.bid,
                        quote.ask,
                        quote.observed_at,
                        quote.trusted,
                    )
                )
        ledger_fills, pending = execution.currency_ledger
        return CurrencyRiskContext(ledger_fills, pending, instruments, tuple(rates))

    for order in execution.orders:
        state_store.backfill_event(
            f"order:{order.order_id}",
            {"action": "PAPER_ORDER", "intent_id": order.intent_id, "reason": "APPROVE"},
        )
    for fill in execution.fills:
        state_store.backfill_event(
            f"fill:{fill.fill_id}",
            {
                "action": "PAPER_FILL",
                "intent_id": "",
                "reason": "PAPER_BROKER" if fill.source == "PAPER_BROKER" else "SIMULATED",
                "trace_id": fill.fill_id,
            },
        )

    def operator_admission() -> None:
        if not state_store.audit_integrity().valid:
            raise HTTPException(status_code=503, detail="Audit integrity verification failed")
        if health_worker is not None:
            try:
                health_worker.verify_admission()
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Health incident journal could not be verified") from error
        try:
            monitoring_journal.history()
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Monitoring incident journal could not be verified") from error

    try:
        operator_sessions = OperatorSessionStore(
            configured_path.with_name(configured_path.stem + ".operator-sessions.sqlite3")
            if configured_path
            else None
        )
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        # Withhold session authority while preserving independently authenticated
        # emergency controls when the paper audit and health journals are sound.
        operator_sessions = None
    operator_authorization = OperatorAuthorization(
        configured_token, operator_sessions, current_time, operator_admission
    )
    operator_authorization.install(app)
    require_operator = operator_authorization.require_operator
    require_control_operator = operator_authorization.require_control
    from ai_quant_os.realtime_feed import RealtimeFeed, install_realtime_routes

    def realtime_capture(now):
        with control_lock:
            return (
                risk.safety_state.value,
                execution.order_count,
                world_state.snapshot(tuple(quotes.copy().values()), now=now),
            )

    install_realtime_routes(
        app, operator_authorization, RealtimeFeed(realtime_capture, clock=current_time)
    )
    install_operator_control_routes(
        app, operator_authorization, state_store, risk, control_lock, current_time
    )
    install_broker_route_control_routes(
        app,
        operator_authorization,
        state_store,
        router,
        risk,
        control_lock,
        current_time,
        persistent=configured_path is not None,
    )

    def require_recovery(x_recovery_token: str | None = Header(default=None)) -> None:
        if not configured_recovery_token:
            raise HTTPException(status_code=503, detail="Paper recovery is disabled")
        if not x_recovery_token or not hmac.compare_digest(
            x_recovery_token, configured_recovery_token
        ):
            raise HTTPException(status_code=401, detail="Invalid recovery token")

    @app.get("/api/v1/paper/evidence/scheduler", dependencies=[Depends(require_operator)])
    def daily_scheduler_status():
        if daily_worker is None:
            return {"schema_version": "1.0", "enabled": False, "state": "DISABLED"}
        try:
            return daily_worker.status()
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Daily scheduler history could not be verified") from error

    @app.get("/api/v1/paper/shadows/scheduler", dependencies=[Depends(require_operator)])
    def shadow_scheduler_status():
        from fastapi.responses import JSONResponse

        if shadow_worker is None:
            value = {"schema_version": "1.0", "enabled": False, "state": "DISABLED"}
        else:
            value = shadow_worker.status()
        return JSONResponse(jsonable_encoder(value), headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/system/status")
    def status() -> dict[str, str | int]:
        return {
            "mode": "PAPER",
            "safety_state": risk.safety_state.value,
            "order_count": len(execution.orders),
        }

    @app.get("/api/v1/risk")
    def risk_state(instrument: str = "EURUSD") -> dict[str, str]:
        if instrument not in instrument_catalog:
            raise HTTPException(status_code=404, detail="Unknown paper instrument")
        exposure = execution.exposure_for(instrument)
        return {
            "instrument_id": instrument,
            "safety_state": risk.safety_state.value,
            "max_order_exposure": str(limits.max_order_exposure),
            "max_portfolio_exposure": str(limits.max_portfolio_exposure),
            "worst_case_quantity": str(exposure.worst_case_quantity),
            "filled_net_quantity": str(exposure.filled_net_quantity),
            "pending_buy_quantity": str(exposure.pending_buy_quantity),
            "pending_sell_quantity": str(exposure.pending_sell_quantity),
            "portfolio_snapshot_id": exposure.snapshot_id,
            "reporting_currency": currency_policy.reporting_currency,
            "max_currency_exposure_reporting": str(currency_policy.max_currency_exposure_reporting),
            "max_gross_currency_exposure_reporting": str(
                currency_policy.max_gross_exposure_reporting
            ),
        }

    @app.get("/api/v1/brokers")
    def brokers() -> dict[str, list[dict[str, str | bool | list[str]]]]:
        return {"brokers": router.snapshot()}

    @app.get("/api/v1/markets/{instrument}")
    def market_detail(instrument: str) -> dict[str, str | bool]:
        snapshot = quotes.get(instrument)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No paper quote for instrument")
        age = (current_time() - snapshot.observed_at).total_seconds()
        freshness = (
            "FRESH" if snapshot.trusted and 0 <= age <= limits.max_quote_age_seconds else "STALE"
        )
        return {
            "instrument_id": snapshot.instrument_id,
            "bid": str(snapshot.bid),
            "ask": str(snapshot.ask),
            "observed_at": snapshot.observed_at.isoformat(),
            "world_state_version": snapshot.world_state_version,
            "trusted": snapshot.trusted,
            "freshness": freshness,
            "source": "PAPER_SIMULATED",
        }

    @app.get("/api/v1/orders")
    def orders() -> dict[str, list[dict[str, str]]]:
        return {"orders": [_order_view(order) for order in execution.orders]}

    @app.get("/api/v1/fills")
    def fills() -> dict[str, list[dict[str, str]]]:
        return {"fills": [_fill_view(fill) for fill in execution.fills]}

    def position_view(
        instrument: str, fills: tuple[PaperFill, ...], quote: MarketSnapshot | None, now: datetime
    ) -> dict[str, str | bool | None]:
        position_fills = tuple(fill for fill in fills if fill.instrument_id == instrument)
        unmarked = summarize_position(position_fills, mark=None)
        mark: Decimal | None = None
        if quote is not None and quote.trusted:
            age = (now - quote.observed_at).total_seconds()
            if 0 <= age <= limits.max_quote_age_seconds:
                mark = quote.bid if unmarked.net_quantity >= 0 else quote.ask
        position = summarize_position(position_fills, mark=mark) if mark is not None else unmarked
        return {
            "mode": "PAPER",
            "instrument_id": instrument,
            "base_currency": instrument_catalog[instrument].base_currency,
            "quote_currency": instrument_catalog[instrument].quote_currency,
            "mark_available": mark is not None,
            "mark_price": str(mark) if mark is not None else None,
            "net_quantity": str(position.net_quantity),
            "cash_flow_quote": str(position.cash_flow_quote),
            "gross_exposure_quote": (
                str(position.gross_exposure_quote)
                if position.gross_exposure_quote is not None
                else None
            ),
            "net_exposure_quote": (
                str(position.net_exposure_quote)
                if position.net_exposure_quote is not None
                else None
            ),
            "marked_pnl_quote": (
                str(position.marked_pnl_quote) if position.marked_pnl_quote is not None else None
            ),
        }

    @app.get("/api/v1/portfolio/snapshot")
    def portfolio_snapshot(instrument: str = "EURUSD") -> dict[str, str | bool | None]:
        if instrument not in instrument_catalog:
            raise HTTPException(status_code=404, detail="Unknown paper instrument")
        return position_view(instrument, execution.fills, quotes.get(instrument), current_time())

    @app.get(
        "/api/v1/paper/evidence/strategy-contributions", dependencies=[Depends(require_operator)]
    )
    def strategy_contributions():
        from pydantic import TypeAdapter
        from fastapi.responses import Response

        try:
            owners, fills = execution.attribution_ledger
            report = build_strategy_contributions(
                owners,
                fills,
                tuple(quotes.copy().values()),
                PAPER_INSTRUMENTS,
                now=current_time(),
                max_age_seconds=limits.max_quote_age_seconds,
            )
            return Response(
                TypeAdapter(type(report)).dump_json(report), media_type="application/json"
            )
        except (ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Paper attribution ledger could not be verified"
            ) from error

    def verified_agent_contribution_inputs():
        owners, fills, intent_run_refs, intent_metadata = execution.agent_attribution_ledger
        run_ids = {run_id for refs in intent_run_refs.values() for run_id in refs}
        runs = {}
        for run_id in run_ids:
            run = state_store.agent_run(run_id)
            if run is None:
                continue
            metadata = tuple(
                intent_metadata[owner.order_id]
                for owner in owners
                if run_id in intent_run_refs[owner.order_id]
            )
            if not metadata or any(
                item["instrument_id"] != run.decision.output.instrument_id
                or item["world_state_version"] != run.snapshot_ref
                or item["created_at"] < run.recorded_at
                or item["created_at"] >= run.deep_review.expires_at
                or item["created_at"] >= run.decision.expires_at
                for item in metadata
            ):
                continue
            snapshot = world_state_store.get(run.snapshot_ref)
            if snapshot is None:
                raise ValueError("Agent run world-state source is missing")
            verify_run_snapshot(run, snapshot)
            runs[run_id] = run
        return owners, fills, intent_run_refs, runs

    @app.get("/api/v1/paper/evidence/agent-contributions", dependencies=[Depends(require_operator)])
    def agent_contributions():
        from pydantic import TypeAdapter
        from fastapi.responses import Response

        from ai_quant_os.agent_attribution import build_agent_contributions

        try:
            owners, fills, intent_run_refs, runs = verified_agent_contribution_inputs()
            report = build_agent_contributions(
                owners, fills, intent_run_refs, runs, PAPER_INSTRUMENTS
            )
            return Response(
                TypeAdapter(type(report)).dump_json(report),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Agent contribution evidence could not be verified"
            ) from error

    @app.get(
        "/api/v1/paper/evidence/agent-contributions/marked",
        dependencies=[Depends(require_operator)],
    )
    def marked_agent_contributions():
        from pydantic import TypeAdapter
        from fastapi.responses import Response

        from ai_quant_os.agent_attribution import build_marked_agent_contributions

        try:
            owners, fills, intent_run_refs, runs = verified_agent_contribution_inputs()
            report = build_marked_agent_contributions(
                owners,
                fills,
                intent_run_refs,
                runs,
                PAPER_INSTRUMENTS,
                tuple(quotes.copy().values()),
                now=current_time(),
                max_age_seconds=limits.max_quote_age_seconds,
            )
            return Response(
                TypeAdapter(type(report)).dump_json(report),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Marked agent contribution evidence could not be verified"
            ) from error

    @app.get(
        "/api/v1/paper/evidence/execution-contributions", dependencies=[Depends(require_operator)]
    )
    def execution_contributions():
        from pydantic import TypeAdapter
        from fastapi.responses import Response

        try:
            owners, fills, benchmarks = execution.execution_evidence_ledger
            report = build_execution_contributions(
                owners, fills, benchmarks, PAPER_INSTRUMENTS, now=current_time()
            )
            return Response(
                TypeAdapter(type(report)).dump_json(report), media_type="application/json"
            )
        except (ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Paper execution evidence could not be verified"
            ) from error

    @app.get("/api/v1/portfolio/positions")
    def portfolio_positions() -> dict[str, object]:
        fills = execution.fills
        observed_quotes = quotes.copy()
        now = current_time()
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "positions": [
                position_view(
                    item.instrument_id, fills, observed_quotes.get(item.instrument_id), now
                )
                for item in PAPER_INSTRUMENTS
            ],
        }

    @app.get("/api/v1/instruments")
    def instruments() -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "instruments": jsonable_encoder(PAPER_INSTRUMENTS),
        }

    @app.get("/api/v1/audit/integrity")
    def audit_integrity() -> dict[str, object]:
        return {"schema_version": "1.0", **jsonable_encoder(state_store.audit_integrity())}

    @app.get("/api/v1/audit")
    def audit_events() -> dict[str, list[dict[str, str]]]:
        try:
            return {"events": state_store.audit_events()}
        except ValueError as error:
            raise HTTPException(
                status_code=503, detail="Audit integrity verification failed"
            ) from error

    @app.get("/api/v1/portfolio/currencies")
    def currency_portfolio() -> dict[str, object]:
        try:
            context = currency_context()
            snapshot = summarize_currency_portfolio(
                context.fills,
                pending=context.pending,
                instruments=context.instruments,
                rates=context.rates,
                reporting_currency=currency_policy.reporting_currency,
                now=current_time(),
                max_rate_age_seconds=currency_policy.max_rate_age_seconds,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            **jsonable_encoder(snapshot, custom_encoder={Decimal: str}),
            "valuation_available": snapshot.valuation_available,
            "mode": "PAPER",
        }

    @app.get("/api/v1/world-state")
    def world_state_snapshot() -> dict[str, object]:
        snapshot = world_state.snapshot(tuple(quotes.copy().values()), now=current_time())
        return {"mode": "PAPER", **jsonable_encoder(snapshot, custom_encoder={Decimal: str})}

    def archive_snapshot(snapshot: WorldState) -> bool:
        try:
            return world_state_store.put(snapshot)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="World-state archive unavailable"
            ) from error

    def archived_view(snapshot: WorldState) -> dict[str, object]:
        now = current_time()
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "archival_only": True,
            "current": snapshot.as_of <= now < snapshot.valid_until,
            "snapshot": jsonable_encoder(snapshot, custom_encoder={Decimal: str}),
        }

    @app.post("/api/v1/paper/world-state/publish", dependencies=[Depends(require_operator)])
    def publish_world_state(
        payload: WorldStatePublishInput = WorldStatePublishInput(),
    ) -> dict[str, object]:
        snapshot = world_state.snapshot(tuple(quotes.copy().values()), now=current_time())
        inserted = archive_snapshot(snapshot)
        state_store.record_event(
            f"world-state-publication:{snapshot.world_state_version}",
            {
                "action": "PAPER_WORLD_STATE_PUBLICATION",
                "intent_id": "",
                "reason": "ARCHIVAL_ONLY",
                "world_state_version": snapshot.world_state_version,
            },
        )
        return {**archived_view(snapshot), "inserted": inserted}

    @app.get("/api/v1/world-state/archive/{world_state_version}")
    def archived_world_state(world_state_version: str) -> dict[str, object]:
        try:
            snapshot = world_state_store.get(world_state_version)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="World-state archive unavailable"
            ) from error
        if snapshot is None:
            raise HTTPException(status_code=404, detail="World-state archive not found")
        return archived_view(snapshot)

    @app.get("/api/v1/decisions/provider")
    def decision_provider_status() -> dict[str, object]:
        status: dict[str, object] = {
            "schema_version": "1.0",
            "mode": "PAPER",
            "configured": configured_classifier is not None,
            "deep_review_configured": configured_reviewer is not None,
            "classification_call_admission": classification_admission.status(),
            "review_call_admission": review_admission.status(),
            "deep_review_provider_id": configured_reviewer.provider_id
            if configured_reviewer
            else None,
            "deep_review_provider_version": configured_reviewer.provider_version
            if configured_reviewer
            else None,
            "provider_id": configured_classifier.provider_id if configured_classifier else None,
            "provider_version": configured_classifier.provider_version
            if configured_classifier
            else None,
        }
        if isinstance(configured_classifier, JevClassifier):
            status.update(configured_classifier.status())
        if isinstance(configured_reviewer, OpenAIReviewer):
            status["deep_review"] = configured_reviewer.status()
        return status

    @app.post("/api/v1/paper/decisions/calibration", dependencies=[Depends(require_operator)])
    def record_calibration(payload: EvaluationBatch) -> dict[str, object]:
        try:
            report = build_calibration_report(
                payload.records,
                provider_id=payload.provider_id,
                provider_version=payload.provider_version,
                dataset_ref=payload.dataset_ref,
                created_at=current_time(),
            )
            calibration_store.put(report)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        state_store.record_event(
            f"calibration-report:{report.report_id}",
            {
                "action": "CALIBRATION_REPORT",
                "intent_id": "",
                "reason": "EVIDENCE_ONLY",
                "report_id": report.report_id,
                "provider_id": report.provider_id,
                "provider_version": report.provider_version,
                "dataset_ref": report.dataset_ref,
            },
        )
        return {"mode": "PAPER", **report.model_dump(mode="json")}

    @app.get("/api/v1/calibration/{report_id}")
    def calibration_report(report_id: str) -> dict[str, object]:
        try:
            report = calibration_store.get(report_id)
        except ValueError as error:
            raise HTTPException(
                status_code=503, detail="Calibration report integrity verification failed"
            ) from error
        if report is None:
            raise HTTPException(status_code=404, detail="Calibration report not found")
        return {"mode": "PAPER", **report.model_dump(mode="json")}

    @app.post("/api/v1/paper/decisions/classify", dependencies=[Depends(require_operator)])
    async def classify_world_state(payload: ClassificationInput) -> dict[str, object]:
        with configured_telemetry.span("paper.market.snapshot"):
            snapshot = world_state.snapshot(tuple(quotes.copy().values()), now=current_time())
            archive_snapshot(snapshot)
        try:
            with configured_telemetry.span("paper.agent.classify") as span:
                decision = await decisions.classify(
                    snapshot, instrument_id=payload.instrument_id, task_id=payload.task_id
                )
                span.set("ai_quant.agent.envelope_id", decision.trace_id)
                span.set("ai_quant.agent.route", decision.output.next_route)
            with configured_telemetry.span("paper.agent.review") as span:
                span.set("ai_quant.agent.parent_envelope_id", decision.trace_id)
                review = await deep_reviews.dispatch(snapshot, decision)
                span.set("ai_quant.agent.envelope_id", review.trace_id)
                span.set("ai_quant.review.status", review.output.status)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        run = AgentRun(
            schema_version="1.0",
            run_id=decision.trace_id,
            snapshot_ref=snapshot.world_state_version,
            decision=decision,
            deep_review=review,
            recorded_at=current_time(),
        )
        try:
            with configured_telemetry.span("paper.agent.archive") as span:
                span.set("ai_quant.agent.envelope_id", run.run_id)
                verify_run_snapshot(run, snapshot)
                trace_binding = make_agent_trace_binding(
                    run, configured_telemetry.binding_context(span)
                )
                if trace_binding is None:
                    state_store.record_agent_run(run)
                else:
                    state_store.record_agent_run(run, trace_binding=trace_binding)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(status_code=503, detail="Agent run storage unavailable") from error
        try:
            require_current_decision(
                decision, world_state_version=snapshot.world_state_version, now=current_time()
            )
            require_current_review(
                review, world_state_version=snapshot.world_state_version, now=current_time()
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "mode": "PAPER",
            **decision.model_dump(mode="json"),
            "deep_review": review.model_dump(mode="json"),
        }

    @app.get("/api/v1/agents/runs/{run_id}")
    def archived_agent_run(run_id: str) -> dict[str, object]:
        try:
            run = state_store.agent_run(run_id)
            snapshot = world_state_store.get(run.snapshot_ref) if run else None
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(status_code=503, detail="Agent run storage unavailable") from error
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found")
        if snapshot is None:
            raise HTTPException(status_code=503, detail="Agent run snapshot unavailable")
        try:
            verify_run_snapshot(run, snapshot)
        except ValueError as error:
            raise HTTPException(
                status_code=503, detail="Agent run provenance unavailable"
            ) from error
        now = current_time()
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "archival_only": True,
            "current": run.deep_review.created_at <= now < run.deep_review.expires_at,
            "run": run.model_dump(mode="json"),
        }

    @app.get("/api/v1/agents/registry")
    def registered_agents() -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "agents": [
                {
                    **item.model_dump(mode="json"),
                    "manifest_ref": item.manifest_ref,
                    "lifecycle": agent_registry.lifecycle(item.agent_id, item.agent_version),
                }
                for item in agent_registry.manifests()
            ],
        }

    @app.post("/api/v1/paper/agents/context", dependencies=[Depends(require_operator)])
    async def compile_agent_context(payload: AgentContextInput) -> dict[str, object]:
        return await compile_context(payload)

    @app.post("/api/v1/paper/tasks/supervise/context", dependencies=[Depends(require_operator)])
    async def compile_task_context(payload: TaskSupervisionInput) -> dict[str, object]:
        return await compile_context(
            AgentContextInput(agent_id="A01", agent_version="0.4.0", task_id=payload.task_id),
            task_kind=payload.task_kind,
        )

    @app.post("/api/v1/paper/incidents/supervise/context", dependencies=[Depends(require_operator)])
    async def compile_incident_task_context(
        payload: IncidentSupervisionInput,
    ) -> dict[str, object]:
        return await compile_context(
            AgentContextInput(agent_id="A01", agent_version="0.5.0", task_id=payload.task_id),
            task_kind="INCIDENT_REVIEW",
        )

    async def compile_context(
        payload: AgentContextInput, *, task_kind: str | None = None
    ) -> dict[str, object]:
        if (payload.agent_id, payload.agent_version) in {
            ("A01", "0.6.0"),
            ("A01", "0.7.0"),
            ("A01", "0.8.0"),
            ("A01", "0.9.0"),
        }:
            raise HTTPException(422, "Typed sandbox research supervision route required")
        if (payload.agent_id, payload.agent_version) == ("A01", "0.4.0") and task_kind is None:
            raise HTTPException(422, "Typed task supervision context route required")
        if (payload.agent_id, payload.agent_version) == (
            "A01",
            "0.5.0",
        ) and task_kind != "INCIDENT_REVIEW":
            raise HTTPException(422, "Typed incident supervision context route required")
        incident_mission = (payload.agent_id, payload.agent_version) == ("A18", "0.2.0")
        supervisor_mission = payload.agent_id == "A01" and payload.agent_version in (
            "0.2.0",
            "0.3.0",
            "0.4.0",
            "0.5.0",
        )
        event_mission = (payload.agent_id, payload.agent_version) == ("A06", "0.2.0")
        macro_mission = (payload.agent_id, payload.agent_version) == ("A03", "0.2.0")
        drift_mission = (payload.agent_id, payload.agent_version) == ("A14", "0.2.0")
        selector_mission = (payload.agent_id, payload.agent_version) == ("A12", "0.2.0")
        selector_v3_mission = (payload.agent_id, payload.agent_version) == ("A12", "0.3.0")
        allocator_mission = (payload.agent_id, payload.agent_version) == ("A13", "0.2.0")
        allocator_v3_mission = (payload.agent_id, payload.agent_version) == ("A13", "0.3.0")
        if payload.upstream_run_id is not None and not allocator_v3_mission:
            raise HTTPException(422, "Upstream graph run is supported only for A13 0.3.0")
        if allocator_v3_mission and payload.upstream_run_id is None:
            raise HTTPException(422, "A13 0.3.0 requires an A12 0.3.0 graph run")
        counterfactual_mission = (payload.agent_id, payload.agent_version) == (
            "A15",
            "0.2.0",
        )
        repair_mission = (payload.agent_id, payload.agent_version) == ("A16", "0.2.0")
        repair_details_mission = (payload.agent_id, payload.agent_version) == (
            "A16",
            "0.3.0",
        )
        repair_trace_mission = (payload.agent_id, payload.agent_version) == (
            "A16",
            "0.4.0",
        )
        evolution_mission = (payload.agent_id, payload.agent_version) == ("A17", "0.2.0")
        context_quotes = tuple(quotes.copy().values())
        snapshot = (
            None
            if event_mission
            or macro_mission
            or supervisor_mission
            or incident_mission
            or drift_mission
            else world_state.snapshot(context_quotes, now=current_time())
        )
        try:
            strength_mission = (payload.agent_id, payload.agent_version) == ("A04", "0.2.0")
            liquidity_mission = (payload.agent_id, payload.agent_version) == ("A05", "0.2.0")
            if drift_mission:
                try:
                    ledger = await asyncio.to_thread(lambda: execution.execution_evidence_ledger)
                    sources = drift_sources(
                        *ledger, now=current_time(), policy=configured_drift_policy
                    )
                except (ValueError, TypeError, OSError, ArithmeticError, sqlite3.Error) as error:
                    raise HTTPException(503, "Drift execution evidence unavailable") from error
            elif incident_mission:
                try:
                    observed = await asyncio.to_thread(capture_incident_observation)
                except (ValueError, TypeError, OSError, sqlite3.Error) as error:
                    raise HTTPException(503, "Incident evidence storage unavailable") from error
                snapshot = world_state.snapshot(context_quotes, now=current_time())
                sources = incident_sources(snapshot, **observed)
            elif supervisor_mission:
                await supervisor_admission()
                try:
                    health = (
                        await asyncio.to_thread(health_worker.status) if health_worker else None
                    )
                except (ValueError, OSError, sqlite3.Error) as error:
                    raise HTTPException(503, "Supervisor health storage unavailable") from error
                snapshot = world_state.snapshot(context_quotes, now=current_time())
                sources = supervisor_sources(
                    snapshot,
                    agent_registry,
                    version=payload.agent_version,
                    task_id=payload.task_id if task_kind is not None else None,
                    task_kind=task_kind,
                    health_status="UNAVAILABLE"
                    if health is None
                    else "ERROR"
                    if health["last_error"]
                    else "MONITORED"
                    if enabled_health
                    else "RESTORED_ONLY",
                    open_incidents=health["open_incidents"] if health else None,
                )
            elif event_mission:
                try:
                    feed, ledger = await asyncio.to_thread(
                        lambda: (event_feed_store.latest(), execution.currency_ledger)
                    )
                except (ValueError, sqlite3.Error) as error:
                    raise HTTPException(
                        status_code=503, detail="Event evidence storage unavailable"
                    ) from error
                sources = event_sources(feed, *ledger, now=current_time())
            elif macro_mission:
                try:
                    capture, feed = await asyncio.to_thread(
                        lambda: (
                            latest_rate_capture(
                                configured_path.parent / "rates.sqlite3"
                                if configured_path
                                else None
                            ),
                            event_feed_store.latest(),
                        )
                    )
                except (ValueError, OSError, sqlite3.Error) as error:
                    raise HTTPException(
                        status_code=503, detail="Macro evidence storage unavailable"
                    ) from error
                snapshot = world_state.snapshot(context_quotes, now=current_time())
                sources = macro_sources(snapshot, capture, feed)
            elif strength_mission:
                sources = currency_strength_sources(snapshot, world_state.histories())
            elif liquidity_mission:
                sources = liquidity_sources(
                    snapshot,
                    world_state.histories(),
                    context_quotes,
                    tuple(displayed_depth.copy().values()),
                )
            elif selector_mission or selector_v3_mission:
                registry_snapshot = await owned_research_call(
                    lambda: _candidate_registry_snapshot(configured_research_workspace)
                )
                source_builder = selector_sources
                shadow_store = None
                if selector_v3_mission:
                    shadow_store = await owned_research_call(
                        lambda: _available_shadow_store(configured_research_workspace)
                    )
                    source_builder = selector_sources_v3
                sources = source_builder(
                    registry_snapshot.candidates,
                    snapshot,
                    now=current_time(),
                    registry_available=registry_snapshot.available,
                    **({"shadow_store": shadow_store} if selector_v3_mission else {}),
                )
            elif allocator_mission or allocator_v3_mission:
                portfolio = await asyncio.to_thread(lambda: execution.exposure_snapshot)
                allocation_inputs = AllocationInputs(
                    portfolio=portfolio,
                    risk_limits=limits,
                    safety_state=risk.safety_state.value,
                )
                if allocator_v3_mission:
                    runtime = require_graph_runtime()
                    try:
                        selector_receipt, selector_output = await asyncio.to_thread(
                            runtime.completed_output, payload.upstream_run_id
                        )
                    except KeyError as error:
                        raise HTTPException(404, "Upstream selector graph run not found") from error
                    except ValueError as error:
                        raise HTTPException(
                            409, "Upstream selector graph run is not current"
                        ) from error
                    if type(selector_output) is not EligibleStrategySetV3:
                        raise HTTPException(409, "Upstream graph run is not A12 0.3.0 output")
                    try:
                        sources = allocator_sources_v3(
                            allocation_inputs,
                            selector_receipt=selector_receipt,
                            selector_output=selector_output,
                            now=current_time(),
                        )
                    except ValueError as error:
                        raise HTTPException(
                            409, "Upstream selector evidence is unavailable"
                        ) from error
                else:
                    sources = allocator_sources(allocation_inputs, now=current_time())
            elif counterfactual_mission:
                report = await owned_research_call(
                    lambda: _available_counterfactual_report(
                        configured_research_workspace, payload.task_id
                    )
                )
                sources = counterfactual_sources(
                    report, replay_id=payload.task_id, now=current_time()
                )
            elif repair_mission or repair_details_mission or repair_trace_mission:
                try:
                    health = (
                        await asyncio.to_thread(health_worker.status) if health_worker else None
                    )
                    incidents = (
                        await asyncio.to_thread(health_worker.store.open_incidents)
                        if health_worker and (repair_details_mission or repair_trace_mission)
                        else None
                    )
                    traces = None
                    runtime = app.state.graph_runtime
                    if repair_trace_mission and incidents is not None and runtime is not None:
                        selected_incidents = sorted(
                            incidents,
                            key=lambda item: (item.detected_at, item.incident_id),
                            reverse=True,
                        )[:8]
                        trace_rows = []
                        expected_status = {
                            "AGENT_FAILED": "FAILED",
                            "AGENT_TIMEOUT": "TIMEOUT",
                            "AGENT_INVALID": "INVALID",
                            "AGENT_UNCERTAIN": "UNCERTAIN",
                        }
                        for incident in selected_incidents:
                            if incident.code not in expected_status:
                                continue
                            try:
                                trace = await asyncio.to_thread(runtime.trace, incident.subject)
                            except KeyError:
                                continue
                            trace_rows.append(
                                RepairTrace(
                                    incident_id=incident.incident_id,
                                    trace_id=trace.trace_id,
                                    scope=trace.scope,
                                    context_ref=trace.context_ref,
                                    agent_id=trace.agent_id,
                                    agent_version=trace.agent_version,
                                    manifest_ref=trace.manifest_ref,
                                    status=trace.status,
                                    event_hashes=tuple(event.event_hash for event in trace.events),
                                    output_sha256=trace.output_sha256,
                                )
                            )
                        traces = tuple(trace_rows)
                except (ValueError, OSError, sqlite3.Error) as error:
                    raise HTTPException(503, "Health evidence unavailable") from error
                sources = repair_sources(
                    RepairInputs(
                        health_status="UNAVAILABLE"
                        if health is None
                        else "ERROR"
                        if health["last_error"]
                        else "MONITORED"
                        if enabled_health
                        else "RESTORED_ONLY",
                        open_incidents=(
                            len(incidents)
                            if (repair_details_mission or repair_trace_mission)
                            and incidents is not None
                            else health["open_incidents"]
                            if health
                            else None
                        ),
                        incidents=incidents,
                        traces=traces,
                    ),
                    now=current_time(),
                    include_incidents=repair_details_mission or repair_trace_mission,
                    include_traces=repair_trace_mission,
                )
            elif evolution_mission:
                quality_receipts = None
                quality_traces = None
                quality_unavailable_reason = "GRAPH_LEDGER_UNAVAILABLE"
                if app.state.graph_runtime is not None:
                    from ai_quant_os.graph_ledger import GraphQualityCapacityExceeded

                    try:
                        quality_receipts, quality_traces = await asyncio.to_thread(
                            app.state.graph_runtime.quality_inventory
                        )
                    except GraphQualityCapacityExceeded:
                        quality_unavailable_reason = "GRAPH_RUN_CAPACITY_EXCEEDED"
                    except (OSError, ValueError, sqlite3.Error):
                        quality_unavailable_reason = "GRAPH_LEDGER_UNAVAILABLE"
                sources = evolution_sources(
                    agent_registry,
                    now=current_time(),
                    quality_receipts=quality_receipts,
                    quality_traces=quality_traces,
                    quality_unavailable_reason=quality_unavailable_reason,
                )
            elif payload.agent_id == "A07":
                sources = integrity_sources(
                    snapshot, world_state.histories(), ingress_health.snapshot()
                )
            else:
                sources = paper_context_sources(snapshot, world_state.histories())
            context = await ContextCompiler(
                agent_registry,
                sources,
                clock=current_time,
                call_budget=call_budget,
                timeout_seconds=2
                if (payload.agent_id, payload.agent_version) == ("A12", "0.3.0")
                else 0.1,
                max_input_bytes=65536
                if drift_mission
                else STRENGTH_INPUT_BYTES
                if strength_mission
                else LIQUIDITY_INPUT_BYTES
                if liquidity_mission
                else EVENT_INPUT_BYTES
                if event_mission
                else MACRO_INPUT_BYTES
                if macro_mission
                else SUPERVISOR_INPUT_BYTES
                if supervisor_mission
                else INCIDENT_INPUT_BYTES
                if incident_mission
                else 8192,
                max_context_bytes=100000
                if drift_mission
                else STRENGTH_CONTEXT_BYTES
                if strength_mission
                else LIQUIDITY_CONTEXT_BYTES
                if liquidity_mission
                else EVENT_CONTEXT_BYTES
                if event_mission
                else MACRO_CONTEXT_BYTES
                if macro_mission
                else SUPERVISOR_CONTEXT_BYTES
                if supervisor_mission
                else INCIDENT_CONTEXT_BYTES
                if incident_mission
                else 32768,
            ).compile(
                payload.agent_id, payload.agent_version, task_id=payload.task_id, scope="PAPER"
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        checkpoint = ContextCheckpoint(
            schema_version="1.0",
            manifest=agent_registry.get(context.agent_id, context.agent_version),
            context=context,
        )
        try:
            state_store.record_context_checkpoint(checkpoint)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Context checkpoint storage unavailable"
            ) from error
        try:
            require_context_for_agent(
                context,
                registry=agent_registry,
                agent_id=payload.agent_id,
                agent_version=payload.agent_version,
                task_id=payload.task_id,
                scope="PAPER",
                now=current_time(),
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail="Agent context admission denied") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "mode": "PAPER",
            **context.model_dump(mode="json"),
            "checkpoint_ref": context.context_ref,
        }

    @app.get("/api/v1/agents/contexts/{context_ref}", dependencies=[Depends(require_operator)])
    def archived_context_checkpoint(context_ref: str) -> dict[str, object]:
        try:
            checkpoint = state_store.context_checkpoint(context_ref)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Context checkpoint storage unavailable"
            ) from error
        if checkpoint is None:
            raise HTTPException(status_code=404, detail="Context checkpoint not found")
        context = checkpoint.context
        now = current_time()
        return {
            "schema_version": "1.0",
            "mode": "PAPER",
            "archival_only": True,
            "current": context.created_at <= now < context.valid_until,
            "checkpoint": checkpoint.model_dump(mode="json"),
        }

    def require_graph_runtime() -> GraphRuntime:
        runtime = app.state.graph_runtime
        if runtime is None:
            raise HTTPException(status_code=503, detail="Persistent agent runtime is unavailable")
        return runtime

    def archived_agent_context(context_ref: str) -> CompiledContext:
        checkpoint = state_store.context_checkpoint(context_ref)
        if checkpoint is None:
            raise ValueError("Supervisor parent context missing")
        return checkpoint.context

    async def compile_research_context(
        task_id: str, agent_version: str = "0.2.0"
    ) -> CompiledContext:
        if agent_version not in {"0.2.0", "0.3.0"}:
            raise ValueError("Unsupported research agent version")
        require_graph_runtime()
        await supervisor_admission()
        agent_registry.require_tool("A08", agent_version, "TRAINING_READ", scope="SANDBOX")

        def capture_research():
            training = research_data_catalog.training(
                agent_registry, "A08", agent_version, scope="SANDBOX"
            )
            graph_path = configured_path.with_name(configured_path.stem + ".agents.sqlite3")
            with GraphLedger.archive(graph_path) as archive:
                records = archive.records_for_agent("A08", agent_version)
                if agent_version == "0.3.0":
                    records = tuple(
                        sorted(
                            (*archive.records_for_agent("A08", "0.2.0"), *records),
                            key=lambda row: (row.receipt.created_at, row.receipt.run_id),
                        )
                    )
                    if len(records) > 128:
                        raise ValueError("Research graph memory exceeds the covered window")
            return training, records, world_state_store.research_snapshots()

        training, records, worlds = await owned_research_call(capture_research)
        context = None
        for _attempt in range(4 if agent_version == "0.3.0" else 1):
            reserved = (
                await asyncio.to_thread(
                    research_novelty_reservations.active_signatures, current_time()
                )
                if agent_version == "0.3.0"
                else ()
            )
            sources = hypothesis_sources(
                training,
                records,
                worlds,
                now=current_time(),
                agent_version=agent_version,
                reserved_signatures=reserved,
            )
            context = await ContextCompiler(
                agent_registry,
                sources,
                clock=current_time,
                call_budget=call_budget,
                max_input_bytes=HYPOTHESIS_INPUT_BYTES,
                max_context_bytes=HYPOTHESIS_CONTEXT_BYTES,
            ).compile("A08", agent_version, task_id=task_id, scope="SANDBOX")
            if context.status != "READY":
                break
            recipe = (
                hypothesis_recipe_v3(context)
                if agent_version == "0.3.0"
                else hypothesis_recipe(context)
            )
            signatures = tuple(sorted(row.signature for row in recipe["hypotheses"]))
            conflicts = await asyncio.to_thread(
                research_novelty_reservations.claim,
                context.context_ref,
                signatures,
                now=current_time(),
                valid_until=context.valid_until,
            )
            if not conflicts:
                break
            context = None
        if context is None:
            raise ValueError("Concurrent research novelty reservation did not stabilize")
        state_store.record_context_checkpoint(
            ContextCheckpoint(
                schema_version="1.0",
                manifest=agent_registry.get("A08", agent_version),
                context=context,
            )
        )
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A08",
            agent_version=agent_version,
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        return context

    async def compile_research_supervisor_context(
        task_id: str,
        agent_version: str = "0.6.0",
        artifact_ref: str | None = None,
        review_ref: str | None = None,
    ) -> CompiledContext:
        require_graph_runtime()
        await supervisor_admission()
        try:
            health = await asyncio.to_thread(health_worker.status) if health_worker else None
        except (ValueError, OSError, sqlite3.Error) as error:
            raise ValueError("Research supervisor health storage unavailable") from error
        snapshot = world_state.snapshot(tuple(quotes.copy().values()), now=current_time())
        if agent_version == "0.8.0":
            if artifact_ref is None:
                raise ValueError("Validation artifact reference required")
            await asyncio.to_thread(
                validator_client.call,
                HoldoutRequest(
                    request_id=uuid4().hex,
                    operation="VALIDATION_CONTEXT",
                    agent_id="A10",
                    agent_version="0.2.0",
                    task_id=task_id,
                    context_ref="context-read:" + uuid4().hex,
                    evidence_ref=artifact_ref,
                ),
            )
        if agent_version == "0.9.0":
            if review_ref is None:
                raise ValueError("Active hypothesis review reference required")

            def require_active_review():
                if strategy_build_store.active(review_ref, now=current_time()) is None:
                    raise KeyError("Active hypothesis review not found")

            await owned_research_call(require_active_review)
        sources = supervisor_sources(
            snapshot,
            agent_registry,
            version=agent_version,
            task_id=task_id,
            task_kind=(
                "VALIDATION_REVIEW"
                if agent_version == "0.8.0"
                else "STRATEGY_CONSTRUCTION"
                if agent_version == "0.9.0"
                else "HYPOTHESIS_RESEARCH"
            ),
            artifact_ref=artifact_ref,
            review_ref=review_ref,
            health_status="UNAVAILABLE"
            if health is None
            else "ERROR"
            if health["last_error"]
            else "MONITORED"
            if enabled_health
            else "RESTORED_ONLY",
            open_incidents=None if health is None else health["open_incidents"],
        )
        context = await ContextCompiler(
            agent_registry,
            sources,
            clock=current_time,
            call_budget=call_budget,
            max_input_bytes=SUPERVISOR_INPUT_BYTES,
            max_context_bytes=SUPERVISOR_CONTEXT_BYTES,
        ).compile("A01", agent_version, task_id=task_id, scope="SANDBOX")
        state_store.record_context_checkpoint(
            ContextCheckpoint(
                schema_version="1.0",
                manifest=agent_registry.get("A01", agent_version),
                context=context,
            )
        )
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A01",
            agent_version=agent_version,
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        return context

    async def observe_research_receipt(receipt) -> None:
        if enabled_health:
            await owned_research_call(health_worker.observe_receipt, receipt)

    def checked_research_graph(run_id: str):
        if configured_path is None:
            raise ValueError("Persistent research graphs required")
        if not state_store.audit_integrity().valid:
            raise ValueError("Research review audit admission failed")
        if health_worker:
            health_worker.verify_admission()
        agent_registry.require_tool("A08", "0.2.0", "TRAINING_READ", scope="SANDBOX")
        graph_path = configured_path.with_name(configured_path.stem + ".agents.sqlite3")
        with GraphLedger.archive(graph_path) as archive:
            return archive.get(run_id)

    def checked_validation_graph(run_id: str):
        if configured_path is None or not state_store.audit_integrity().valid:
            raise ValueError("Persistent checked graph admission required")
        if health_worker:
            health_worker.verify_admission()
        graph_path = configured_path.with_name(configured_path.stem + ".agents.sqlite3")
        with GraphLedger.archive(graph_path) as archive:
            return archive.get(run_id)

    async def compile_builder_context(task_id: str, review_ref: str) -> CompiledContext:
        require_graph_runtime()
        await supervisor_admission()
        agent_registry.require_tool("A09", "0.2.0", "CANDIDATE_CREATE", scope="SANDBOX")

        def capture():
            review = strategy_build_store.active(review_ref, now=current_time())
            return builder_sources(review, now=current_time())

        sources = await owned_research_call(capture)
        context = await ContextCompiler(
            agent_registry,
            sources,
            clock=current_time,
            call_budget=call_budget,
            max_input_bytes=65536,
            max_context_bytes=100000,
        ).compile("A09", "0.2.0", task_id=task_id, scope="SANDBOX")
        state_store.record_context_checkpoint(
            ContextCheckpoint(
                schema_version="1.0", manifest=agent_registry.get("A09", "0.2.0"), context=context
            )
        )
        await owned_research_call(
            lambda: strategy_build_store.active(review_ref, now=current_time())
        )
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A09",
            agent_version="0.2.0",
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        return context

    async def compile_validation_context(task_id: str, artifact_ref: str) -> CompiledContext:
        require_graph_runtime()
        await supervisor_admission()
        agent_registry.require_tool("A10", "0.2.0", "VALIDATION_RUN", scope="SANDBOX")
        context_request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="VALIDATION_CONTEXT",
            agent_id="A10",
            agent_version="0.2.0",
            task_id=task_id,
            context_ref="context-read:" + uuid4().hex,
            evidence_ref=artifact_ref,
        )
        response = await asyncio.to_thread(validator_client.call, context_request)
        snapshot_envelope = response.context_snapshot
        if type(snapshot_envelope) is not HoldoutContextSnapshot or (
            snapshot_envelope.agent_id != "A10"
        ):
            raise ValueError("Validation service omitted its checked context snapshot")
        snapshot = ValidationContextSnapshot.model_validate_json(snapshot_envelope.payload_json)
        summary = snapshot.summary
        if (
            response.evidence_ref != artifact_ref
            or response.summary_sha256 != summary.summary_sha256
            or response.aggregate.outcome != summary.outcome
            or response.aggregate.gate_count != len(summary.gates)
        ):
            raise ValueError("Validation service context differs from its checked summary")
        context = await ContextCompiler(
            agent_registry,
            validation_sources_from_summary(
                summary,
                snapshot.holdout_receipt,
                captured_at=snapshot.captured_at,
                now=current_time(),
            ),
            clock=current_time,
            call_budget=call_budget,
            max_input_bytes=16384,
            max_context_bytes=65536,
        ).compile("A10", "0.2.0", task_id=task_id, scope="SANDBOX")
        if any(row.status == "DENIED" for row in context.inputs):
            raise PermissionError("Validation evidence fetch admission denied")
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A10",
            agent_version="0.2.0",
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        if summary_for_context(context) != summary:
            raise ValueError("Validation result changed during context capture")
        state_store.record_context_checkpoint(
            ContextCheckpoint(
                schema_version="1.0", manifest=agent_registry.get("A10", "0.2.0"), context=context
            )
        )
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A10",
            agent_version="0.2.0",
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        return context

    async def compile_adversarial_context(task_id: str, experiment_id: str) -> CompiledContext:
        require_graph_runtime()
        await supervisor_admission()
        agent_registry.require_tool("A11", "0.2.0", "STRESS_RUN", scope="SANDBOX")
        context_request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="STRESS_CONTEXT",
            agent_id="A11",
            agent_version="0.2.0",
            task_id=task_id,
            context_ref="context-read:" + uuid4().hex,
            evidence_ref=experiment_id,
        )
        response = await asyncio.to_thread(validator_client.call, context_request)
        snapshot_envelope = response.context_snapshot
        if type(snapshot_envelope) is not HoldoutContextSnapshot or (
            snapshot_envelope.agent_id != "A11"
        ):
            raise ValueError("Adversarial service omitted its checked context snapshot")
        snapshot = AdversarialContextSnapshot.model_validate_json(snapshot_envelope.payload_json)
        stress = snapshot.stress
        from ai_quant_os.holdout_validator_rpc import stress_summary_sha256

        if (
            response.evidence_ref != experiment_id
            or snapshot.candidate.experiment_id != experiment_id
            or response.summary_sha256
            != stress_summary_sha256(stress.state, stress.reason, stress.scenarios)
            or response.aggregate.state != stress.state
            or response.aggregate.reason_codes != (stress.reason,)
            or response.aggregate.scenario_count != len(stress.scenarios)
            or tuple(row.model_dump(mode="json") for row in response.aggregate.scenarios)
            != tuple(row.model_dump(mode="json") for row in stress.scenarios)
        ):
            raise ValueError("Adversarial service context differs from checked stress summary")
        context = await ContextCompiler(
            agent_registry,
            adversarial_sources_from_snapshot(
                snapshot.candidate,
                snapshot.validation,
                snapshot.stress,
                holdout_instrument_id=snapshot.holdout_instrument_id,
                holdout_first_observed_at=snapshot.holdout_first_observed_at,
                holdout_last_observed_at=snapshot.holdout_last_observed_at,
                holdout_dataset_fingerprint=snapshot.holdout_dataset_fingerprint,
                now=current_time(),
            ),
            clock=current_time,
            call_budget=call_budget,
            max_input_bytes=16384,
            max_context_bytes=65536,
        ).compile("A11", "0.2.0", task_id=task_id, scope="SANDBOX")
        if any(row.status == "DENIED" for row in context.inputs):
            raise PermissionError("Adversarial evidence fetch admission denied")
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A11",
            agent_version="0.2.0",
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        state_store.record_context_checkpoint(
            ContextCheckpoint(
                schema_version="1.0",
                manifest=agent_registry.get("A11", "0.2.0"),
                context=context,
            )
        )
        require_context_for_agent(
            context,
            registry=agent_registry,
            agent_id="A11",
            agent_version="0.2.0",
            task_id=task_id,
            scope="SANDBOX",
            now=current_time(),
        )
        return context

    def capture_incident_observation() -> dict:
        incidents = health_worker.store.open_incidents() if health_worker else None
        health = health_worker.status() if health_worker else None
        broker = next(row for row in router.snapshot() if row["broker_id"] == "PAPER")
        return dict(
            incidents=incidents,
            watchdog_status="UNAVAILABLE"
            if health is None
            else "ERROR"
            if health["last_error"]
            else "MONITORED"
            if enabled_health
            else "RESTORED_ONLY",
            broker=broker,
            orders=execution.orders,
            local_fills=execution.fills,
            broker_fills=router.all_fills("PAPER"),
            live_state=risk.safety_state,
            durable_state=state_store.safety_state(),
        )

    async def supervisor_admission() -> None:
        if not state_store.audit_integrity().valid:
            raise ValueError("Supervisor audit admission failed")
        if health_worker is not None:
            await asyncio.to_thread(health_worker.verify_admission)

    async def observe_supervisor_child(receipt) -> None:
        if enabled_health:
            await owned_research_call(health_worker.observe_receipt, receipt)

    async def compile_supervisor_child(agent_id: str, task_id: str) -> CompiledContext:
        if agent_id == "A08":
            return await compile_research_context(task_id)
        try:
            result = await compile_agent_context(
                AgentContextInput(
                    agent_id=agent_id,
                    agent_version="0.2.0",
                    task_id=task_id,
                )
            )
        except HTTPException as error:
            if error.status_code == 403:
                raise PermissionError("Specialist context admission denied") from error
            raise ValueError("Specialist context compilation unavailable") from error
        return CompiledContext.model_validate(
            {key: value for key, value in result.items() if key not in {"mode", "checkpoint_ref"}}
        )

    async def compile_supervisor_child_versioned(
        agent_id: str, agent_version: str, task_id: str
    ) -> CompiledContext:
        if (agent_id, agent_version) == ("A08", "0.3.0"):
            return await compile_research_context(task_id, "0.3.0")
        raise PermissionError("Unsupported versioned supervisor child")

    async def compile_supervisor_child_parent_aware(
        parent: CompiledContext, agent_id: str, agent_version: str, source_ref: str
    ) -> CompiledContext:
        if (agent_id, agent_version) != ("A10", "0.2.0") and (agent_id, agent_version) != (
            "A09",
            "0.2.0",
        ):
            raise PermissionError("Unsupported bound supervisor child")
        if (parent.agent_id, parent.agent_version, parent.scope) == ("A01", "0.8.0", "SANDBOX"):
            if (agent_id, agent_version) != ("A10", "0.2.0"):
                raise PermissionError("Unsupported validation supervisor child")
            from ai_quant_os.task_supervisor_research import read_context_v4

            if read_context_v4(parent).task_spec.artifact_ref != source_ref:
                raise PermissionError("Validation artifact differs from declared parent task")
            return await compile_validation_context(parent.task_id, source_ref)
        if (parent.agent_id, parent.agent_version, parent.scope) == ("A01", "0.9.0", "SANDBOX"):
            if (agent_id, agent_version) != ("A09", "0.2.0"):
                raise PermissionError("Unsupported construction supervisor child")
            from ai_quant_os.task_supervisor_research import read_context_v5

            if read_context_v5(parent).task_spec.review_ref != source_ref:
                raise PermissionError("Review differs from declared parent task")
            return await compile_builder_context(parent.task_id, source_ref)
        raise PermissionError("Bound child requires its supported A01 parent")

    @app.post("/api/v1/paper/agents/execute", dependencies=[Depends(require_operator)])
    async def execute_agent_graph(payload: AgentGraphInput) -> dict[str, object]:
        runtime = require_graph_runtime()
        try:
            checkpoint = state_store.context_checkpoint(payload.context_ref)
            if checkpoint is None:
                raise HTTPException(status_code=404, detail="Context checkpoint not found")
            if checkpoint.context.scope != "PAPER":
                raise HTTPException(status_code=403, detail="Paper context required")
            receipt = await runtime.run(payload.run_id, checkpoint.context)
            if enabled_health:
                await owned_health_call(health_worker.observe_receipt, receipt)
            context_current = runtime.current(receipt.run_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="Graph run rejected") from error
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="Graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "advisory_only": True,
            "context_current": context_current,
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.get(
        "/api/v1/agents/graph-runs",
        response_model=GraphRunPage,
        dependencies=[Depends(require_operator)],
    )
    def archived_graph_page(
        after_run_id: str = "", head: str | None = None, limit: int = 25
    ) -> dict[str, object]:
        if (
            not isinstance(after_run_id, str)
            or (after_run_id and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", after_run_id) is None)
            or not 1 <= limit <= 25
            or (head is not None and re.fullmatch(r"[a-f0-9]{64}", head) is None)
            or (after_run_id and head is None)
        ):
            raise HTTPException(422, "Invalid graph archive page")
        try:
            return (
                require_graph_runtime()
                .archive_page(after_run_id=after_run_id, head=head, limit=limit)
                .model_dump(mode="json")
            )
        except GraphArchiveChanged as error:
            raise HTTPException(409, "Graph archive changed; restart pagination") from error
        except KeyError as error:
            raise HTTPException(404, "Graph archive cursor not found") from error
        except (ValueError, TypeError, sqlite3.Error) as error:
            raise HTTPException(503, "Graph archive unavailable") from error

    @app.get("/api/v1/agents/graph-runs/{run_id}", dependencies=[Depends(require_operator)])
    def archived_graph_run(run_id: str) -> dict[str, object]:
        runtime = require_graph_runtime()
        try:
            receipt = runtime.receipt(run_id)
            context_current = runtime.current(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Graph run not found") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(status_code=503, detail="Graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "archival_only": True,
            "context_current": context_current,
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.get(
        "/api/v1/agents/graph-runs/{run_id}/trace",
        response_model=GraphTrace,
        dependencies=[Depends(require_operator)],
    )
    def archived_graph_trace(run_id: str) -> dict[str, object]:
        try:
            return require_graph_runtime().trace(run_id).model_dump(mode="json")
        except KeyError as error:
            raise HTTPException(404, "Graph run not found") from error
        except (ValueError, TypeError, sqlite3.Error) as error:
            raise HTTPException(503, "Graph trace unavailable") from error

    @app.post(
        "/api/v1/paper/agents/graph-runs/{run_id}/resume", dependencies=[Depends(require_operator)]
    )
    async def resume_agent_graph(run_id: str) -> dict[str, object]:
        runtime = require_graph_runtime()
        try:
            original = runtime.receipt(run_id)
            checkpoint = state_store.context_checkpoint(original.context_ref)
            if checkpoint is None or checkpoint.context.scope != "PAPER":
                raise HTTPException(status_code=403, detail="Paper context required")
            receipt = await runtime.resume(run_id)
            if enabled_health:
                await owned_health_call(health_worker.observe_receipt, receipt)
            context_current = runtime.current(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Graph run not found") from error
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(status_code=503, detail="Graph storage unavailable") from error
        return {
            "mode": "PAPER",
            "advisory_only": True,
            "context_current": context_current,
            "receipt": receipt.model_dump(mode="json"),
        }

    @app.post("/api/v1/paper/world-state/bars", dependencies=[Depends(require_operator)])
    def record_world_state_bars(payload: WorldStateBarsInput) -> dict[str, object]:
        try:
            bars = tuple(Bar(**item.model_dump()) for item in payload.bars)
            for bar in bars:
                require_paper_prices(bar.instrument_id, (bar.open, bar.high, bar.low, bar.close))
            now = current_time()
            if any(bar.observed_at > now for bar in bars):
                raise ValueError("World-state bars cannot be future-dated")
            world_state_store.record_bars(bars, audit_event_key=f"world-state-bars:{uuid4()}")
            inserted, duplicates = world_state.record_bars_with_health(bars, now=now)
        except ValueError as error:
            message = str(error)
            if "Market bar archive conflict" in message or "Conflicting duplicate" in message:
                ingress_health.note("CONFLICTING_BAR")
                raise HTTPException(status_code=422, detail=message) from error
            if any(
                marker in message.lower()
                for marker in ("archive", "capacity exhausted", "integrity", "audit")
            ):
                raise HTTPException(503, "Market history archive unavailable") from error
            code = "FUTURE_BAR" if "future-dated" in message else "INVALID_BAR"
            ingress_health.note(code)
            raise HTTPException(status_code=422, detail=message) from error
        except sqlite3.Error as error:
            raise HTTPException(503, "Market history archive unavailable") from error
        ingress_health.note("DUPLICATE_BAR", duplicates)
        return {"mode": "PAPER", "inserted_count": inserted}

    @app.post("/api/v1/paper/quotes", dependencies=[Depends(require_operator)])
    def record_quote(quote: QuoteInput) -> dict[str, str]:
        try:
            snapshot = MarketSnapshot(**quote.model_dump(), trusted=True)
            require_paper_prices(snapshot.instrument_id, (snapshot.bid, snapshot.ask))
        except ValueError as error:
            ingress_health.note("INVALID_QUOTE")
            raise HTTPException(status_code=422, detail=str(error)) from error
        quotes[snapshot.instrument_id] = snapshot
        displayed_depth.pop(snapshot.instrument_id, None)
        return {"instrument_id": snapshot.instrument_id, "mode": "PAPER"}

    @app.post("/api/v1/paper/l1-quotes", dependencies=[Depends(require_operator)])
    def record_l1_quote(payload: PaperL1QuoteInput) -> dict[str, str]:
        try:
            snapshot = MarketSnapshot(**payload.quote.model_dump(), trusted=True)
            require_paper_prices(snapshot.instrument_id, (snapshot.bid, snapshot.ask))
            depth = DisplayedDepth(
                instrument_id=snapshot.instrument_id,
                observed_at=snapshot.observed_at,
                quote_ref="paper-quote:" + fingerprint(asdict(snapshot)),
                bid_size=payload.bid_size,
                ask_size=payload.ask_size,
            )
        except ValueError as error:
            ingress_health.note("INVALID_QUOTE")
            raise HTTPException(status_code=422, detail=str(error)) from error
        quotes[snapshot.instrument_id] = snapshot
        displayed_depth[snapshot.instrument_id] = depth
        return {
            "instrument_id": snapshot.instrument_id,
            "mode": "PAPER",
            "provenance": depth.provenance,
        }

    @app.post("/api/v1/paper/candidate-assignments", dependencies=[Depends(require_operator)])
    def assign_candidate_to_paper(payload: CandidatePaperAssignmentInput):
        from ai_quant_os.research_store import encode_research

        if payload.expires_at.tzinfo is None or payload.expires_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Assignment expiry must include a timezone")
        strategy_ref = f"{payload.strategy_id}@{payload.strategy_version}"
        existing = execution.candidate_paper_assignment(payload.assignment_id)
        if existing is not None:
            if (
                existing.candidate_id != payload.candidate_id
                or existing.strategy_ref != strategy_ref
                or existing.instrument_id != payload.instrument_id
                or existing.max_exposure != payload.max_exposure
                or existing.expires_at != payload.expires_at.astimezone(timezone.utc)
            ):
                raise HTTPException(
                    status_code=409, detail="Candidate paper assignment is immutable"
                )
            return json.loads(encode_research(existing))
        candidate_store = paper_candidate_store()
        if candidate_store is None:
            raise HTTPException(status_code=409, detail="Candidate registry is unavailable")
        snapshot = candidate_store.snapshot(payload.candidate_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Research candidate not found")
        from ai_quant_os.candidate_paper import create_candidate_paper_assignment

        view, bundle = snapshot
        try:
            assignment = create_candidate_paper_assignment(
                payload.assignment_id,
                view,
                bundle,
                strategy_id=payload.strategy_id,
                strategy_version=payload.strategy_version,
                instrument_id=payload.instrument_id,
                max_exposure=payload.max_exposure,
                assigned_at=current_time(),
                expires_at=payload.expires_at,
            )
            retained = execution.save_candidate_paper_assignment(assignment)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return json.loads(encode_research(retained))

    @app.get(
        "/api/v1/paper/candidate-assignments/{assignment_id}",
        dependencies=[Depends(require_operator)],
    )
    def get_candidate_paper_assignment(assignment_id: str):
        try:
            assignment = execution.candidate_paper_assignment(assignment_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if assignment is None:
            raise HTTPException(status_code=404, detail="Candidate paper assignment not found")
        from ai_quant_os.research_store import encode_research

        return json.loads(encode_research(assignment))

    def authorize_candidate_paper_intent(intent, assignment_id, *, now):
        if assignment_id is None:
            return
        assignment = execution.candidate_paper_assignment(assignment_id)
        candidate_store = paper_candidate_store()
        snapshot = (
            candidate_store.snapshot(assignment.candidate_id)
            if candidate_store is not None and assignment is not None
            else None
        )
        if assignment is None or snapshot is None:
            raise ValueError("Current candidate paper assignment evidence is unavailable")
        view, bundle = snapshot
        assignment.authorize(intent, assignment_id, view, bundle, now=now)

    def _submit_intent(
        payload: IntentInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str | None]:
        try:
            intent = TradeIntent(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            previous = execution.replay(
                intent,
                idempotency_key=idempotency_key,
                candidate_assignment_id=payload.candidate_assignment_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if previous is not None:
            with configured_telemetry.span("paper.order.replay") as span:
                span.set("ai_quant.order.id", previous.order_id)
                span.set("ai_quant.order.status", previous.status)
            if previous.status == "PENDING":
                try:
                    authorize_candidate_paper_intent(
                        intent, payload.candidate_assignment_id, now=current_time()
                    )
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                quote = quotes.get(intent.instrument_id)
                if quote is None:
                    raise HTTPException(status_code=409, detail="No paper market quote")
                try:
                    with configured_telemetry.span("paper.order.recover") as span:
                        previous = execution.recover_pending(
                            intent,
                            idempotency_key=idempotency_key,
                            market=quote,
                            now=current_time(),
                            currency_context=currency_context(quote),
                            candidate_assignment_id=payload.candidate_assignment_id,
                        )
                        span.set("ai_quant.order.id", previous.order_id)
                        span.set("ai_quant.order.status", previous.status)
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                if previous.status == "ACCEPTED":
                    state_store.record_event(
                        f"order:{previous.order_id}",
                        {
                            "action": "PAPER_ORDER",
                            "intent_id": intent.intent_id,
                            "reason": "APPROVE",
                        },
                    )
            return _order_view(previous)
        try:
            authorize_candidate_paper_intent(
                intent, payload.candidate_assignment_id, now=current_time()
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        with configured_telemetry.span("paper.market.snapshot"):
            quote = quotes.get(intent.instrument_id)
        if quote is None:
            raise HTTPException(status_code=409, detail="No paper market quote")
        now = current_time()
        try:
            with configured_telemetry.span("paper.risk.evaluate") as span:
                decision = risk.evaluate(
                    intent,
                    quote,
                    now=now,
                    portfolio=execution.exposure_for(intent.instrument_id),
                    currency_context=currency_context(quote),
                )
                span.set("ai_quant.risk.decision", decision.decision)
            with configured_telemetry.span("paper.order.submit") as span:
                order = execution.submit(
                    intent,
                    decision,
                    idempotency_key=idempotency_key,
                    now=now,
                    candidate_assignment_id=payload.candidate_assignment_id,
                )
                if order is not None:
                    span.set("ai_quant.order.id", order.order_id)
                    span.set("ai_quant.order.status", order.status)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if order is None:
            state_store.record_event(
                f"risk:{decision.risk_id}",
                {
                    "action": "RISK_REJECT",
                    "intent_id": intent.intent_id,
                    "reason": ",".join(decision.violations),
                },
            )
            return {
                "order_id": None,
                "intent_id": intent.intent_id,
                "risk_id": decision.risk_id,
                "status": decision.decision,
                "broker_route": "PAPER",
            }
        state_store.record_event(
            f"order:{order.order_id}",
            {"action": "PAPER_ORDER", "intent_id": intent.intent_id, "reason": "APPROVE"},
        )
        return _order_view(order)

    @app.post(
        "/api/v1/paper/intents",
        dependencies=[Depends(require_operator), Depends(candidate_admission_guard)],
    )
    def submit_intent(
        payload: IntentInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str | None]:
        assignment_id = payload.candidate_assignment_id
        if assignment_id is None:
            return _submit_intent(payload, idempotency_key)
        assignment = execution.candidate_paper_assignment(assignment_id)
        if assignment is None:
            raise HTTPException(status_code=409, detail="Candidate paper assignment not found")
        candidate_store = paper_candidate_store()
        if candidate_store is None:
            raise HTTPException(status_code=409, detail="Candidate registry is unavailable")
        with candidate_store.admission_snapshot(assignment.candidate_id):
            return _submit_intent(payload, idempotency_key)

    @app.post("/api/v1/paper/fills", dependencies=[Depends(require_operator)])
    def record_fill(payload: FillInput) -> dict[str, str]:
        try:
            fill = execution.record_fill(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"fill:{fill.fill_id}",
            {
                "action": "PAPER_FILL",
                "intent_id": "",
                "reason": "SIMULATED",
                "trace_id": fill.fill_id,
            },
        )
        return _fill_view(fill)

    @app.post("/api/v1/paper/broker-fills", dependencies=[Depends(require_operator)])
    def simulate_broker_fill(payload: FillInput) -> dict[str, str]:
        try:
            fill = execution.simulate_broker_fill(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"broker-fill:{fill.fill_id}",
            {
                "action": "PAPER_BROKER_FILL_RECORDED",
                "intent_id": "",
                "reason": "AWAITING_SYNC",
                "trace_id": fill.fill_id,
            },
        )
        return _fill_view(fill)

    @app.post(
        "/api/v1/paper/orders/{order_id}/sync-fills", dependencies=[Depends(require_operator)]
    )
    def sync_broker_fills(order_id: str) -> dict[str, str | int]:
        try:
            imported = execution.sync_broker_fills(order_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        for fill in execution.fills:
            if fill.order_id == order_id and fill.source == "PAPER_BROKER":
                state_store.record_event(
                    f"fill:{fill.fill_id}",
                    {
                        "action": "PAPER_FILL",
                        "intent_id": "",
                        "reason": "PAPER_BROKER",
                        "trace_id": fill.fill_id,
                    },
                )
        return {"order_id": order_id, "imported_count": imported}

    @app.post("/api/v1/paper/positions/reconcile", dependencies=[Depends(require_operator)])
    def reconcile_paper_positions() -> dict[str, object]:
        try:
            result = execution.compare_paper_positions()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        state_store.record_event(
            f"position-reconciliation:{uuid4()}",
            {
                "action": "PAPER_POSITION_RECONCILIATION",
                "intent_id": "",
                "reason": "MATCH" if result.matches else "MISMATCH",
                "local_net_quantity": str(result.local_net_quantity),
                "broker_net_quantity": str(result.broker_net_quantity),
                "local_cash_flow_quote": str(result.local_cash_flow_quote),
                "broker_cash_flow_quote": str(result.broker_cash_flow_quote),
                "local_only_fill_ids": ",".join(result.local_only_fill_ids),
                "broker_only_fill_ids": ",".join(result.broker_only_fill_ids),
                "conflicting_fill_ids": ",".join(result.conflicting_fill_ids),
                "mismatched_instruments": ",".join(
                    item.instrument_id for item in result.instruments if not item.matches
                ),
            },
        )
        return {
            "schema_version": "1.1",
            "summary_instrument_id": "EURUSD",
            "instruments": jsonable_encoder(result.instruments, custom_encoder={Decimal: str}),
            "matches": result.matches,
            "local_net_quantity": str(result.local_net_quantity),
            "broker_net_quantity": str(result.broker_net_quantity),
            "local_cash_flow_quote": str(result.local_cash_flow_quote),
            "broker_cash_flow_quote": str(result.broker_cash_flow_quote),
            "local_only_fill_ids": list(result.local_only_fill_ids),
            "broker_only_fill_ids": list(result.broker_only_fill_ids),
            "conflicting_fill_ids": list(result.conflicting_fill_ids),
        }

    @app.post("/api/v1/paper/orders/{order_id}/reconcile", dependencies=[Depends(require_operator)])
    def reconcile_paper_order(order_id: str) -> dict[str, str]:
        try:
            order = execution.reconcile_order(order_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if order.status in {"ACCEPTED", "REJECTED"}:
            state_store.record_event(
                f"reconcile:{order.order_id}",
                {
                    "action": "PAPER_ORDER_RECONCILED",
                    "intent_id": order.intent_id,
                    "reason": order.status,
                },
            )
        return _order_view(order)

    def transition(state: SafetyState, command: ControlInput, key: str) -> dict[str, str]:
        reason = command.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Reason is required")
        with control_lock:
            previous = state_store.control_receipt(key)
            if previous is not None:
                if previous[0] != state or previous[1] != reason:
                    raise HTTPException(status_code=409, detail="Idempotency key reused")
                return previous[2]
            receipt = {
                "receipt_id": str(uuid4()),
                "status": "COMPLETED",
                "safety_state": state.value,
                "reason": reason,
            }
            try:
                risk.set_safety_state(
                    state,
                    before_change=lambda: state_store.record_control(key, state, reason, receipt),
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            return receipt

    @app.post("/api/v1/control/no-new-risk", dependencies=[Depends(require_control_operator)])
    def no_new_risk(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        return transition(SafetyState.NO_NEW_RISK, command, idempotency_key)

    @app.post("/api/v1/control/flatten-only", dependencies=[Depends(require_control_operator)])
    def flatten_only(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        return transition(SafetyState.FLATTEN_ONLY, command, idempotency_key)

    @app.post("/api/v1/control/halt", dependencies=[Depends(require_control_operator)])
    def halt(command: ControlInput, idempotency_key: str = Header(min_length=1)) -> dict[str, str]:
        return transition(SafetyState.HALT, command, idempotency_key)

    @app.post(
        "/api/v1/control/recover-paper",
        dependencies=[Depends(require_control_operator), Depends(require_recovery)],
    )
    def recover_paper(
        command: ControlInput, idempotency_key: str = Header(min_length=1)
    ) -> dict[str, str]:
        reason = command.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Reason is required")
        with control_lock:
            previous = state_store.recovery_receipt(idempotency_key)
            if previous is not None:
                if previous[0] != reason:
                    raise HTTPException(status_code=409, detail="Idempotency key reused")
                return previous[1]
            receipt = {
                "receipt_id": str(uuid4()),
                "status": "COMPLETED",
                "safety_state": SafetyState.NORMAL.value,
                "reason": reason,
            }
            try:
                from ai_quant_os.paper_recovery import require_completed_triage

                require_completed_triage(state_store.audit_events())
                if health_worker is not None and health_worker.store.open_incidents():
                    raise ValueError("Open health incidents require independently gated repair")
                execution.recover_paper_safety(
                    before_change=lambda: state_store.record_recovery(
                        idempotency_key, reason, receipt
                    )
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            return receipt

    install_recovery_routes(app, require_operator)
    install_event_feed_routes(app, event_feed_store, require_operator, current_time)
    install_health_routes(app, require_operator, health_worker, enabled_health)
    from ai_quant_os.mt5_sync_health_api import install_demo_sync_health_routes

    install_demo_sync_health_routes(
        app, require_operator, configured_demo_sync_state_dir, current_time
    )
    from ai_quant_os.broker_capability_api import install_broker_capability_routes

    install_broker_capability_routes(app, router, require_operator, clock=current_time)
    install_repair_temporal_routes(app, require_operator)
    install_incident_proposal_routes(app, require_operator, incident_proposal_store)
    install_research_data_routes(app, research_data_catalog, require_operator, current_time)
    install_validation_package_routes(
        app,
        require_operator,
        preparation=ValidationPreparation(
            strategy_build_store, research_data_catalog, validation_package_store
        ),
        archive=validation_package_store,
        graph_record=checked_validation_graph,
        clock=current_time,
    )
    install_validation_scientist_routes(
        app,
        require_operator,
        store=candidate_validation_store,
        compile_context=compile_validation_context,
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
    )
    install_adversarial_routes(
        app,
        require_operator,
        compile_context=compile_adversarial_context,
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
    )
    install_builder_routes(
        app,
        require_operator,
        store=strategy_build_store,
        graph_record=checked_research_graph,
        clock=current_time,
        compile_context=compile_builder_context,
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
        require_build_permission=lambda: agent_registry.require_tool(
            "A09", "0.2.0", "CANDIDATE_CREATE", scope="SANDBOX"
        ),
    )
    install_hypothesis_routes(
        app,
        require_operator,
        compile_context=compile_research_context,
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
    )
    install_research_supervisor_routes(
        app,
        require_operator,
        compile_context=compile_research_supervisor_context,
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
    )
    install_research_supervisor_routes(
        app,
        require_operator,
        compile_context=lambda task_id: compile_research_supervisor_context(task_id, "0.7.0"),
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
        route_prefix="/api/v1/sandbox/research/supervise/v3",
        agent_version="0.7.0",
    )
    from ai_quant_os.task_supervisor_research import ValidationResearchSupervisionInput

    install_research_supervisor_routes(
        app,
        require_operator,
        compile_context=lambda task_id, artifact_ref: compile_research_supervisor_context(
            task_id, "0.8.0", artifact_ref
        ),
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
        route_prefix="/api/v1/sandbox/research/supervise/v4",
        agent_version="0.8.0",
        context_input_model=ValidationResearchSupervisionInput,
    )
    from ai_quant_os.task_supervisor_research import ConstructionResearchSupervisionInput

    install_research_supervisor_routes(
        app,
        require_operator,
        compile_context=lambda task_id, review_ref: compile_research_supervisor_context(
            task_id, "0.9.0", review_ref=review_ref
        ),
        archived_context=archived_agent_context,
        runtime=require_graph_runtime,
        observe_receipt=observe_research_receipt,
        route_prefix="/api/v1/sandbox/research/supervise/v5",
        agent_version="0.9.0",
        context_input_model=ConstructionResearchSupervisionInput,
    )
    install_daily_routes(
        app,
        configured_path,
        require_operator,
        lambda: execution.attribution_ledger,
        lambda: execution.execution_evidence_ledger,
        lambda: tuple(quotes.copy().values()),
        PAPER_INSTRUMENTS,
        current_time,
        configured_research_workspace,
        configured_demo_sync_state_dir,
    )
    install_research_routes(
        app,
        configured_research_workspace,
        require_operator,
        candidate_admission_guard,
    )
    install_agent_call_routes(app, call_budget, require_operator)
    install_spending_routes(
        app,
        configured_reviewer if isinstance(configured_reviewer, OpenAIReviewer) else None,
        require_operator,
        current_time,
    )
    install_shared_spending_routes(
        app,
        tuple(
            SpendingSource(provider)
            for provider in (configured_reviewer, configured_classifier)
            if isinstance(provider, (OpenAIReviewer, JevClassifier))
        ),
        require_operator,
        current_time,
    )
    install_tracing_status_routes(app, configured_telemetry, require_operator, current_time)
    install_paper_metrics_routes(app, configured_telemetry, require_operator)
    monitoring_alert_source = monitoring_alert_source or MonitoringAlertSource(
        enabled=os.getenv("AI_QUANT_PROMETHEUS_ALERTS_ENABLED") == "1"
    )
    install_monitoring_alert_routes(
        app,
        monitoring_alert_source,
        require_operator,
        monitoring_journal if configured_path is not None else None,
    )
    install_monitoring_journal_routes(
        app,
        monitoring_journal if configured_path is not None else None,
        operator_authorization.require_session,
        operator_authorization.authenticate_session,
        current_time,
    )
    install_agent_trace_binding_routes(app, state_store, require_operator, current_time)
    install_collector_trace_routes(
        app,
        CollectorTraceSource(
            collector_trace_file
            or (
                Path(os.environ["AI_QUANT_COLLECTOR_TRACE_FILE"])
                if os.getenv("AI_QUANT_COLLECTOR_TRACE_FILE")
                else None
            )
        ),
        require_operator,
        current_time,
    )
    install_evolution_routes(
        app,
        configured_evolution_workspace,
        require_operator,
        evolution_scheduler,
    )
    return app


app = create_app()
