# Backend module layers and enforced dependencies

## Primary layers

The primary layer of every inventoried Python package module is listed in `.clean/architecture.json`. The `test_primary_layers_cover_every_package_module_once` check requires the layer lists to form an exact, disjoint partition of `package_module_inventory`.

| Layer | Current responsibility | Examples |
| --- | --- | --- |
| `domain` | Canonical paper types and pure market, risk, portfolio, currency, and instrument rules | `contracts.py`, `risk.py`, `currency_risk.py`, `paper_instruments.py` |
| `control` | Deterministic broker admission, paper execution, market ingress, and safety recovery | `broker.py`, `execution.py`, `paper_market_ingress.py`, `paper_recovery.py` |
| `api` | HTTP route modules and API composition root | `api.py`, `operator_control_api.py`, feature `*_api.py` modules |
| `agents` | Agent policy, contexts, registry, graph execution, and agent implementations | `graph_runtime.py`, `agent_context.py`, `evolution_director_agent.py` |
| `strategies` | Strategy definitions, builder/selector, candidate lifecycle, and strategy execution research | `strategy_builder.py`, `strategy_selector_agent.py`, `strategy_prefix_execution.py` |
| `persistence` | Durable stores, repositories, journals, catalogs, and audit chain | `paper_state_store.py`, `order_store.py`, `audit_chain.py` |
| `integrations` | Provider, broker-demo, data, workflow, cache, telemetry, and object-store adapters | `jev_adapter.py`, `mt5_sync_service.py`, `valkey_cache.py` |
| `application` | Remaining orchestration, evaluation, CLI, and feature services | `local_runtime.py`, `research_pipeline.py`, `candidate_cli.py` |

These are primary ownership labels for a large modular package, not deployment or process boundaries. Feature stores and their typed contracts may share a feature layer; this does not mean the package has a pure, universally enforced three-tier architecture.

## Enforced import rules

`.clean/architecture.json` declares the source and forbidden target layers for each rule. `tests/test_architecture_broker_boundary.py::test_declared_layer_import_rules_hold_for_current_sources` parses the current package sources and fails closed on unresolved dynamic imports in restricted layers.

| Rule | Boundary | Enforcement |
| --- | --- | --- |
| `domain-purity` | Domain modules do not import other layers or infrastructure prefixes | Domain source inventory and import checks |
| `control-authority-isolation` | Control modules do not import API, agent, or strategy layers | Layer import test |
| `agent-and-strategy-control-isolation` | Agent and strategy modules do not import deterministic control modules | Layer test plus agent/strategy broker-boundary guard |
| `persistence-interface-isolation` | Persistence modules do not import API modules | Layer import test |
| `integration-interface-isolation` | Integrations do not import API modules | Layer import test |

The API layer is the outer composition boundary and may call authenticated application/control paths. The application layer orchestrates features and is not a separate dependency-restricted ring yet. The current checks do not prove indirect call-graph safety, runtime-constructed imports outside recognized Python forms, API authorization coverage for every route, or provider/network isolation. The UI has its own static browser network-boundary check.

## Verification and limits

The architecture suite covers inventory, complete primary-layer assignment, criticality partition, current forbidden edges, and import detector behavior. A temporary agent-to-control import was injected into a fresh archive checkout; the layer test failed with the exact offending module and import, then the committed source remained unchanged. This is a first enforceable layer boundary, not complete architecture acceptance. Broader feature contracts, call-graph analysis, and independent P00 review remain open.