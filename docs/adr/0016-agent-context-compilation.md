# 0016: Bounded, source-bound agent context compilation

## Decision and contract

Implement ContextCompiler against the versioned agent registry. Compile only a manifest's declared input names from server-owned SourceBinding adapters. Clients cannot supply source bindings, dataset classes, evidence payloads, permissions or lifecycle. Every context identifies exact agent/version, manifest content reference, task, scope, creation/deadline, input records and content references. All contexts remain advisory and require escalation, including READY contexts.

Inputs have explicit AVAILABLE, MISSING, DENIED, STALE, FUTURE, UNTRUSTED, TIMEOUT, FAILED or BUDGET_EXCEEDED status. An unavailable input contains no payload, source identity or content reference. Missing evidence is not replaced with a guessed value. READY means all declared inputs are present, trusted, permitted and within the context deadline; it does not mean market suitability, correctness, agent implementation readiness or trading authorization.

EvidenceRecord canonicalizes JSON-object payloads and UTC timestamps, requires aware positive source validity, and computes a content reference over source metadata and payload. Payloads remain immutable strings in the context rather than mutable nested dictionaries. Context references cover the full canonical bundle. Integrity checks validate schema/status, evidence references, source byte identity and validity. Hashes detect changes but are not signatures or proof of independent source truth.

## Dataset and permission boundaries

Each SourceBinding names an allowed read tool and a GENERAL/TRAINING/HOLDOUT resource class. The compiler checks both the read capability and any TRAINING_READ/HOLDOUT_READ requirement. Thus a holdout source accessed through HISTORY_READ or WORLD_STATE_READ still requires holdout permission and SANDBOX scope. Generators cannot obtain holdout payloads through a general read capability. Denied sources are never fetched.

Bindings and dataset labels are trusted server declarations. A malicious or incorrectly labelled source adapter could put evaluation data into a GENERAL payload; physical dataset catalogs, immutable classification metadata and storage isolation remain open. The compiler cannot prove source honesty or isolate hostile Python. No dataset storage adapter or live feed is installed by this increment.

Permissions are checked before source invocation, after queued admission and again before publishing fetched evidence. A quarantined agent cannot fetch new evidence, and revocation during a fetch withholds its return. Consumers must use require_context_for_agent: it rechecks exact task/agent/version/scope, manifest reference and required input names, source tools/classes and current lifecycle. No tool or order authority is conveyed by the context.

## Deadlines and budgets

Defaults are four concurrent reads, 100 ms per read, two seconds overall, 8 KiB per input and 32 KiB for the serialized context. Deadline expiration and caller cancellation cancel pending cooperative source tasks and await cleanup. Non-cooperative code still requires process isolation; this interface is not an operating-system sandbox.

Recheck source freshness after all reads, so evidence expiring while other inputs load is withheld. Context expiry is at most five seconds from compilation and no later than any published source deadline. Source-future/untrusted/expired data is withheld. These are byte budgets, not precise model-token budgets. Oversized inputs are withheld whole, never silently truncated. If the bundle exceeds its total budget, later manifest inputs are withheld first; metadata-only overflow rejects compilation.

## Paper API and installed sources

Authenticated POST /api/v1/paper/agents/context accepts agent_id, agent_version and task_id only. It compiles in PAPER scope from a single server-generated paper snapshot, records context/manifest references in audit, and rechecks expiry and admission after audit persistence. It does not call a model, create an order or execute an agent.

Installed read sources are world_state, currency_twins, available fresh price_features and available fresh volatility_features. World-state evidence can describe EMPTY/DEGRADED market conditions; source availability does not make contained stale quote values fresh. Price/volatility adapters emit only currently usable corresponding features. Session, macro, dataset, strategy and other missing sources remain explicit. The endpoint does not archive full contexts or manifests yet; audit records their references. Durable graph-context checkpoints are the next dependency.

## Verification and rollback

Tests cover reproducibility, source references, source-bound expiry, missing inputs, all generator holdout denials, sandbox validator access, future/stale/untrusted/failed/timed-out evidence, expiry during collection, concurrent admission/deadline cancellation, input/total budgets, tampering, quarantine/revocation, exact consumer identity and expiry during audit. API checks reject client source fields and verify absence of order creation.

No database migration, external call or dependency change is introduced. Rollback reverts compiler/source adapters and API wiring while retaining existing advisory result, registry and audit evidence. Full graph runtime, resource/catalog isolation, source adapters, context persistence and complete agent missions remain unfinished.
