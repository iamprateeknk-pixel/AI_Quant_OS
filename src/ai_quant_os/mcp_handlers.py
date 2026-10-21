"""Real paper-system handlers for MCP catalog tools (V3.2 section 11).

Phase 2 registered exactly one handler (``get_risk_state``). This module
adds handlers for the catalog tools that have an honest source in the
paper composition root, behind an explicit bindings object:

* a handler is built only when its binding is present in
  :class:`McpToolBindings`; every other tool stays unregistered and the
  registry keeps failing it closed with ``LookupError`` (HTTP 409);
* read handlers return real current data from the bound store/service
  (Decimals are rendered as strings, like the rest of the paper API);
* simulate handlers are pure computations over real current state plus
  the caller's scenario, label their inputs in the result, and never
  mutate the bound state;
* shadow deploy/pause/compare have no in-app service whose signature
  the catalog arguments (ids only) can honestly invoke -- deployment
  needs a full frozen ``ShadowAssignment`` and retirement needs a
  command id, a reason and a clock -- so they are exposed only as
  optional service callables a composition root may supply. The
  default paper app supplies none, so they stay 409 there.

Bindings are structural (duck-typed): this module imports no broker,
execution or order-store code, so the tool fabric cannot reach order
placement through it. There is deliberately no binding field that
could carry an order, a credential or a secret.

Tools that remain unregistered even with full direct bindings, and
why (no honest in-app source invocable from the catalog arguments):

* ``create_hypothesis`` / ``create_experiment`` -- hypothesis creation
  requires a compiled agent context and graph custody; no store or
  service creates either from the catalog arguments.
* ``run_backtest`` / ``run_walk_forward`` / ``compare_runs`` -- the
  backtest engines need a live strategy object and dataset bound by
  the isolated worker; no in-app service resolves version ids to one,
  and there is no queryable backtest-run archive in the composition
  root.
* ``run_monte_carlo`` / ``leakage_scan`` / ``stress_regime`` /
  ``capacity_test`` -- these are gates inside the checked candidate
  validation evaluation, which requires the full validation package,
  test result, holdout and window reports; none can be invoked from a
  bare candidate id, and recomputing over paper fills would attribute
  paper trades to a candidate.
* ``create_branch`` / ``run_tests`` -- no branch or test-runner
  service exists in the app; candidate tests run in isolated worker
  custody over a full test bundle.
* ``build_strategy_artifact`` -- ``StrategyBuildStore.put_candidate``
  enforces builder-graph custody (an active hypothesis review plus
  exact ``render_candidate`` recipe equality); writing from a bare
  candidate id would bypass that custody.
* ``create_approval_request`` -- no generic approval-request store
  exists; the paper state store's operator reservations are custody
  for specific operator-control receipts, not generic requests.
* ``replay_session`` -- the signal-tape replay needs a full candidate
  dataset package and the Nautilus engine; there is no session store
  keyed by session id.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Safety ladder in declaration order, mirroring ``risk.SafetyState``.
_SAFETY_LADDER = (
    "NORMAL",
    "CAUTION",
    "REDUCED_RISK",
    "NO_NEW_RISK",
    "FLATTEN_ONLY",
    "HALT",
)

#: States the gated paper recovery workflow may return to NORMAL.
_RECOVERABLE_STATES = frozenset({"CAUTION", "REDUCED_RISK", "NO_NEW_RISK"})


@dataclass(frozen=True)
class McpToolBindings:
    """Optional paper-system collaborators for MCP tool handlers.

    Every field defaults to ``None``; a tool's handler is registered
    only when the field(s) it needs are present. Fields are typed as
    ``Any`` on purpose: the composition root passes the real stores
    and services, and handlers use only their documented read surface
    (``list()``, ``incidents()``, ``snapshot()``, ``fills``,
    ``exposure_for()``, ``audit_event()``, policy attributes). The
    three ``*_fn`` fields are service callables for the shadow tools,
    whose catalog arguments are ids only; a composition root that owns
    a real shadow deployment service adapts it to these signatures.
    """

    research_catalog: Any = None
    execution: Any = None
    broker_router: Any = None
    health_store: Any = None
    risk_policy: Any = None
    risk_kernel: Any = None
    paper_state_store: Any = None
    deploy_shadow_fn: Callable[[str], Mapping[str, Any]] | None = None
    pause_shadow_fn: Callable[[str], Mapping[str, Any]] | None = None
    compare_challenger_fn: Callable[[str, str], Mapping[str, Any]] | None = None


def _str_decimal(value: Any) -> str:
    return str(value)


def _decimal_arg(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValueError(f"Argument {field!r} must be a decimal value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Argument {field!r} must be a decimal value") from error
    if not parsed.is_finite():
        raise ValueError(f"Argument {field!r} must be finite")
    return parsed


def _object_arg(value: Any, field: str) -> dict:
    """Accept a mapping, or a JSON string encoding one (schema type)."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError as error:
            raise ValueError(f"Argument {field!r} must be a JSON object") from error
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"Argument {field!r} must be an object")


def _fill_rows(execution: Any) -> list[dict]:
    """Group the gateway's real fills per instrument (read-only)."""
    grouped: dict[str, dict] = {}
    for fill in execution.fills:
        row = grouped.setdefault(
            fill.instrument_id,
            {
                "instrument_id": fill.instrument_id,
                "fill_count": 0,
                "net_quantity": Decimal(0),
                "cash_flow_quote": Decimal(0),
                "_price_quantity": Decimal(0),
                "_quantity": Decimal(0),
            },
        )
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        cash = (
            -fill.quantity * fill.price - fill.fee
            if fill.side == "BUY"
            else (fill.quantity * fill.price - fill.fee)
        )
        row["fill_count"] += 1
        row["net_quantity"] += signed
        row["cash_flow_quote"] += cash
        row["_price_quantity"] += fill.quantity * fill.price
        row["_quantity"] += fill.quantity
    return [grouped[name] for name in sorted(grouped)]


def _public_fill_row(row: dict) -> dict:
    return {
        "instrument_id": row["instrument_id"],
        "fill_count": row["fill_count"],
        "net_quantity": _str_decimal(row["net_quantity"]),
        "cash_flow_quote": _str_decimal(row["cash_flow_quote"]),
    }


def _exposure_view(exposure: Any) -> dict:
    return {
        "snapshot_id": exposure.snapshot_id,
        "instrument_id": exposure.instrument_id,
        "filled_net_quantity": _str_decimal(exposure.filled_net_quantity),
        "pending_buy_quantity": _str_decimal(exposure.pending_buy_quantity),
        "pending_sell_quantity": _str_decimal(exposure.pending_sell_quantity),
        "worst_case_quantity": _str_decimal(exposure.worst_case_quantity),
    }


def _query_dataset(catalog: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        dataset_id = arguments["dataset_id"]
        for receipt in catalog.list():
            if receipt.dataset_ref == dataset_id:
                if hasattr(receipt, "model_dump"):
                    payload = receipt.model_dump(mode="json")
                else:
                    payload = {"dataset_ref": receipt.dataset_ref}
                return {"mode": "PAPER", "dataset": payload}
        raise LookupError(f"Unknown research dataset: {dataset_id}")

    return handler


def _get_exposure(execution: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        snapshot = execution.exposure_snapshot
        rows = _fill_rows(execution)
        instruments = []
        seen = set()
        for name in [snapshot.instrument_id] + [row["instrument_id"] for row in rows]:
            if name in seen:
                continue
            seen.add(name)
            view = _exposure_view(execution.exposure_for(name))
            fill_row = next((row for row in rows if row["instrument_id"] == name), None)
            view["fill_count"] = fill_row["fill_count"] if fill_row else 0
            view["cash_flow_quote"] = _str_decimal(fill_row["cash_flow_quote"]) if fill_row else "0"
            instruments.append(view)
        return {
            "mode": "PAPER",
            "snapshot": _exposure_view(snapshot),
            "instruments": instruments,
            "order_count": execution.order_count,
            "fill_count": sum(row["fill_count"] for row in rows),
        }

    return handler


def _simulate_allocation(execution: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        weights_raw = _object_arg(arguments["weights"], "weights")
        if not weights_raw:
            raise ValueError("simulate_allocation requires at least one weight")
        weights: dict[str, Decimal] = {}
        for name, raw in weights_raw.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Allocation instrument ids must be non-empty strings")
            weight = _decimal_arg(raw, f"weights.{name}")
            if weight < 0:
                raise ValueError("Allocation weights cannot be negative")
            weights[name] = weight
        total_weight = sum(weights.values(), Decimal(0))
        if total_weight <= 0:
            raise ValueError("Allocation weights must sum to a positive value")
        rows = _fill_rows(execution)
        current = {row["instrument_id"]: row["net_quantity"] for row in rows}
        gross = sum((abs(value) for value in current.values()), Decimal(0))
        targets = []
        for name in sorted(set(weights) | set(current)):
            weight = weights.get(name, Decimal(0)) / total_weight
            target = weight * gross
            targets.append(
                {
                    "instrument_id": name,
                    "weight": _str_decimal(weight),
                    "current_net_quantity": _str_decimal(current.get(name, Decimal(0))),
                    "target_net_quantity": _str_decimal(target),
                    "delta_quantity": _str_decimal(target - current.get(name, Decimal(0))),
                }
            )
        return {
            "mode": "SIMULATION",
            "mutates_state": False,
            "basis": "long_only_fraction_of_current_gross_quantity",
            "current_gross_quantity": _str_decimal(gross),
            "fill_count": sum(row["fill_count"] for row in rows),
            "targets": targets,
        }

    return handler


def _stress_portfolio(execution: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        scenario = _object_arg(arguments["scenario"], "scenario")
        uniform = scenario.get("shock_bps")
        shocks_raw = scenario.get("shocks") or {}
        if uniform is None and not shocks_raw:
            raise ValueError("Stress scenario requires 'shock_bps' or 'shocks'")
        if not isinstance(shocks_raw, Mapping):
            raise ValueError("Stress scenario 'shocks' must be an object")
        rows = _fill_rows(execution)
        results = []
        total_change = Decimal(0)
        for row in rows:
            raw_shock = shocks_raw.get(row["instrument_id"], uniform)
            if raw_shock is None:
                continue
            shock_bps = _decimal_arg(raw_shock, "shock_bps")
            if row["_quantity"] == 0:
                continue
            vwap = row["_price_quantity"] / row["_quantity"]
            change = row["net_quantity"] * vwap * (shock_bps / Decimal(10_000))
            total_change += change
            results.append(
                {
                    "instrument_id": row["instrument_id"],
                    "net_quantity": _str_decimal(row["net_quantity"]),
                    "mark_basis": "quantity_weighted_average_fill_price",
                    "mark_price": _str_decimal(vwap),
                    "shock_bps": _str_decimal(shock_bps),
                    "stressed_pnl_change_quote": _str_decimal(change),
                }
            )
        return {
            "mode": "SIMULATION",
            "mutates_state": False,
            "scenario": {
                key: (str(value) if not isinstance(value, Mapping) else dict(value))
                for key, value in scenario.items()
            },
            "instruments": results,
            "total_stressed_pnl_change_quote": _str_decimal(total_change),
        }

    return handler


def _simulate_risk_change(kernel: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        proposal = _object_arg(arguments["proposal"], "proposal")
        current_state = str(getattr(kernel.safety_state, "value", kernel.safety_state))
        result: dict[str, Any] = {
            "mode": "SIMULATION",
            "applies_change": False,
            "current_safety_state": current_state,
        }
        handled = False
        if "safety_state" in proposal:
            handled = True
            target = proposal["safety_state"]
            if target not in _SAFETY_LADDER:
                raise ValueError(f"Unknown safety state: {target!r}")
            if target == current_state:
                classification = "NO_CHANGE"
            elif _SAFETY_LADDER.index(target) > _SAFETY_LADDER.index(current_state):
                classification = "ESCALATION_PERMITTED_BY_KERNEL_LADDER"
            elif target == "NORMAL" and current_state in _RECOVERABLE_STATES:
                classification = "REQUIRES_GATED_PAPER_RECOVERY_WORKFLOW"
            else:
                classification = "REFUSED_BY_KERNEL_LADDER"
            result["proposed_safety_state"] = target
            result["safety_state_assessment"] = classification
        if "max_portfolio_exposure" in proposal:
            handled = True
            current_limit = kernel.max_portfolio_exposure
            proposed_limit = _decimal_arg(
                proposal["max_portfolio_exposure"], "max_portfolio_exposure"
            )
            result["current_max_portfolio_exposure"] = _str_decimal(current_limit)
            result["proposed_max_portfolio_exposure"] = _str_decimal(proposed_limit)
            result["limit_delta"] = _str_decimal(proposed_limit - current_limit)
            result["limit_assessment"] = "REQUIRES_NEW_VERSIONED_RISK_POLICY"
        if not handled:
            raise ValueError("Risk proposal requires 'safety_state' or 'max_portfolio_exposure'")
        return result

    return handler


def _get_broker_health(router: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        broker_id = arguments["broker_id"]
        for row in router.snapshot():
            if row.get("broker_id") == broker_id:
                eligibility = router.eligibility(broker_id)
                return {
                    "mode": "PAPER",
                    "broker_id": broker_id,
                    "connection_state": row.get("connection_state"),
                    "enabled": row.get("enabled"),
                    "instruments": row.get("instruments"),
                    "eligibility": {
                        "eligible": eligibility.eligible,
                        "compliance_scope": eligibility.compliance_scope,
                        "allows_paper_orders": eligibility.allows_paper_orders(),
                    },
                }
        raise LookupError(f"Unknown broker route: {broker_id}")

    return handler


def _get_incident(store: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        incident_id = arguments["incident_id"]
        for incident in store.incidents():
            if incident.incident_id == incident_id:
                return {
                    "mode": "PAPER",
                    "incident_id": incident.incident_id,
                    "code": incident.code,
                    "subject": incident.subject,
                    "evidence_json": incident.evidence_json,
                    "detected_at": incident.detected_at.isoformat(),
                    "safety_state": incident.safety_state,
                    "quarantine": incident.quarantine,
                    "agent_id": incident.agent_id,
                    "agent_version": incident.agent_version,
                    "manifest_ref": incident.manifest_ref,
                }
        raise LookupError(f"Unknown incident: {incident_id}")

    return handler


def _get_policy(policy: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        policy_id = arguments["policy_id"]
        if policy_id != policy.policy_version:
            raise LookupError(f"Unknown policy: {policy_id}")
        return {
            "mode": "PAPER",
            "policy_id": policy.policy_version,
            "policy_version": policy.policy_version,
            "digest": policy.digest,
            "max_order_exposure": _str_decimal(policy.max_order_exposure),
            "max_portfolio_exposure": _str_decimal(policy.max_portfolio_exposure),
            "max_quote_age_seconds": policy.max_quote_age_seconds,
            "reporting_currency": policy.reporting_currency,
            "max_currency_exposure_reporting": _str_decimal(policy.max_currency_exposure_reporting),
            "max_gross_exposure_reporting": _str_decimal(policy.max_gross_exposure_reporting),
            "max_rate_age_seconds": policy.max_rate_age_seconds,
        }

    return handler


def _export_evidence(store: Any) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        evidence_id = arguments["evidence_id"]
        event = store.audit_event(evidence_id)
        if event is None:
            raise LookupError(f"Unknown evidence record: {evidence_id}")
        integrity = store.audit_integrity()
        return {
            "mode": "PAPER",
            "evidence_id": evidence_id,
            "event": event,
            "audit_integrity": {
                "valid": integrity.valid,
                "event_count": integrity.event_count,
                "head_hash": integrity.head_hash,
            },
        }

    return handler


def _service_callable(service: Callable, *keys: str) -> Callable[[dict], dict]:
    def handler(arguments: dict) -> dict:
        result = service(*(arguments[key] for key in keys))
        if result is None:
            raise LookupError("Bound shadow service found no matching record")
        if not isinstance(result, Mapping):
            raise ValueError("Bound shadow service returned a non-object result")
        return {"mode": "PAPER", **dict(result)}

    return handler


def build_tool_handlers(bindings: McpToolBindings | None) -> dict[str, Callable]:
    """Build handlers for exactly the tools ``bindings`` can serve.

    Returns an empty mapping for ``bindings=None``. ``get_risk_state``
    is not built here: ``paper_mcp_service`` always registers it from
    its own ``risk`` argument, exactly as in Phase 2.
    """
    if bindings is None:
        return {}
    handlers: dict[str, Callable] = {}
    if bindings.research_catalog is not None:
        handlers["query_dataset"] = _query_dataset(bindings.research_catalog)
    if bindings.execution is not None:
        handlers["get_exposure"] = _get_exposure(bindings.execution)
        handlers["simulate_allocation"] = _simulate_allocation(bindings.execution)
        handlers["stress_portfolio"] = _stress_portfolio(bindings.execution)
    if bindings.risk_kernel is not None:
        handlers["simulate_risk_change"] = _simulate_risk_change(bindings.risk_kernel)
    if bindings.broker_router is not None:
        handlers["get_broker_health"] = _get_broker_health(bindings.broker_router)
    if bindings.health_store is not None:
        handlers["get_incident"] = _get_incident(bindings.health_store)
    if bindings.risk_policy is not None:
        handlers["get_policy"] = _get_policy(bindings.risk_policy)
    if bindings.paper_state_store is not None:
        handlers["export_evidence"] = _export_evidence(bindings.paper_state_store)
    if bindings.deploy_shadow_fn is not None:
        handlers["deploy_shadow"] = _service_callable(bindings.deploy_shadow_fn, "candidate_id")
    if bindings.pause_shadow_fn is not None:
        handlers["pause_shadow"] = _service_callable(bindings.pause_shadow_fn, "candidate_id")
    if bindings.compare_challenger_fn is not None:
        handlers["compare_challenger"] = _service_callable(
            bindings.compare_challenger_fn, "challenger_id", "champion_id"
        )
    return handlers


__all__ = [
    "McpToolBindings",
    "build_tool_handlers",
]
