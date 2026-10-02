# AI Quant OS

This repository starts the execution pack as a **local, paper-only** trading foundation. It accepts simulated quotes and trade intents, checks deterministic risk rules, records paper order receipts, and exposes a local control API. It does not connect to Exness, MT5, any broker, or live capital.

## Run locally

Use Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
$env:AI_QUANT_OPERATOR_TOKEN = "<generate-a-random-local-token>"
$env:AI_QUANT_PAPER_DB = ".\paper.sqlite3"
.\.venv\Scripts\python -m uvicorn ai_quant_os.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for the API. Without `AI_QUANT_OPERATOR_TOKEN`, all write commands return 503. Read endpoints show the paper mode, risk state, orders, and audit events. Do not expose this development server beyond localhost.

The Mission Control frontend is in `apps/web`. In a second terminal:

```powershell
cd apps/web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. The frontend reads `AI_QUANT_API_URL` on the server and defaults to `http://127.0.0.1:8000`. It displays unavailable states for services not connected yet. It does not expose control commands until operator-session authentication is built.

## Native backtests

Install the optional native runtime with `python -m pip install -e ".[dev,data,trading]"`. Run `ai-quant-backtest tests/fixtures/nautilus_replay.csv --strategy moving-average-cross --slippage-bps 10 --fee 0.10 --output native-result.json`. This uses the actual pinned Nautilus engine with a declared synthetic open/close quote model and produces a source-bound result artifact. It grants no live order authority. See [the native runtime handoff](docs/NAUTILUS_HANDOFF.md) for costs, P&L rounding, benchmark evidence and remaining gates.
ai-quant-backtest tests/fixtures/nautilus_ticks.csv --format ticks --strategy tick-momentum --fee 0.10 --output docs/evaluations/nautilus-tick-result.json

## Durable paper recovery

The optional `recovery` extra adds an actual Temporal workflow for paper broker uncertainty. It persists containment and attempt evidence, performs acknowledgement lookup without resending orders, survives worker/service restart, and stops at review with trading still disabled. Opt-in application wiring uses the same risk kernel and control lock, with protected start/read/retry commands. Unfinished triage also blocks manual normalization. See [the recovery handoff](docs/TEMPORAL_RECOVERY_HANDOFF.md) for setup, verification and remaining P10 requirements.

## Checks

An opt-in health watchdog (`AI_QUANT_HEALTH_WATCHDOG_ENABLED=1`) checks actual paper and graph sources, journals failures independently, and persists exact-version agent quarantine. Existing journal pins restore even with monitoring disabled, and open incidents block manual safety recovery. See [the health handoff](docs/HEALTH_WATCHDOG_HANDOFF.md) for failure taxonomy, protected inspection commands and incident runbooks.

`ai-quant-repair` builds an isolated candidate branch from a quarantined A02/A07 incident and retains its original source as the rollback target. Candidate inspection checks provenance; independent verification, shadow/canary and release remain pending. See [repair candidate handoff](docs/REPAIR_CANDIDATE_HANDOFF.md).

The read-only `ai-quant-repair sandbox-status` command checks local isolated-runtime prerequisites without executing candidate code. Live enforcement and behavioral verification remain unverified; see [sandbox admission handoff](docs/REPAIR_SANDBOX_HANDOFF.md).

`ai-quant-repair snapshot` inspects complete committed package hashes for a repair candidate. Trusted-parent job preparation retains those exact bytes and a pinned verifier program without executing either. See [source/job handoff](docs/REPAIR_SOURCE_JOB_HANDOFF.md) for pending execution and incident-replay requirements.

`ai-quant-repair replay <candidate-id> --graph-store <archive>` verifies the original failed invocation's receipt and captured inline inputs through read-only archival access. It reports hashes and retains historical expiry; it executes no repair. See [archived input handoff](docs/REPAIR_REPLAY_HANDOFF.md).

`ai-quant-repair admit` prepares an owned job, creates and inspects a local pinned container, then removes it without starting it. The exact request is recorded before creation and the response before admission. Unconfirmed cleanup preserves the job and blocks further staging in that job root. See [container ownership handoff](docs/REPAIR_CONTAINER_HANDOFF.md) and [creation journal handoff](docs/REPAIR_CREATE_JOURNAL_HANDOFF.md); live confinement and repair execution remain unverified.

`ai-quant-repair cleanup` reconstructs a retained job from its committed snapshot and pinned verifier, validates its ownership receipt and files, and removes a matching container before deleting inputs. A committed exact removal acknowledgement also permits retry after lease release or interrupted file deletion; each surviving file is verified before cleanup. Unknown absence remains unresolved. See [restart cleanup handoff](docs/REPAIR_CLEANUP_HANDOFF.md) and [removal journal handoff](docs/REPAIR_REMOVAL_JOURNAL_HANDOFF.md).

`ai-quant-repair cleanup-staging` verifies and removes orphan inputs using their staging receipt and independent source/suite pins. Pending container outcomes block this command. See [staging recovery handoff](docs/REPAIR_STAGING_HANDOFF.md).

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest -q
cd apps/web
npm run lint
npm run build
```

The optional local data stack starts PostgreSQL, ClickHouse, Valkey, and an S3 emulator with `python scripts/start_data_stack.py`. Run `python scripts/check_data_stack.py` to verify the services and integration tests. See [data/LOCAL_STACK.md](data/LOCAL_STACK.md).

## Current boundaries

- The risk kernel owns quote freshness, trust, spread, world-state match, intent expiry, order exposure, portfolio exposure, and safety state checks. For each of the EURUSD, GBPUSD, USDJPY and EURJPY paper routes, the fixed ceilings are 1,000 base units per order and 1,500 worst case base units across its filled position and pending orders. Unit quantities are kept separate by instrument. The gateway rechecks the exact portfolio snapshot inside the SQLite order transaction. The API also enforces a fixed USD-valued ceiling of 2,000 per currency and 4,000 gross across both currency legs. Currency prices and ledger state are checked again at reservation and before dispatch.
- The paper execution gateway accepts only decisions issued by its risk kernel, allows one order per intent, commits a reservation before calling the simulator, retains accepted simulator acknowledgement IDs, and deduplicates retries. Rejected orders remain as receipts and release reserved exposure. An exception during broker placement creates an `UNKNOWN` outcome, holds exposure, and escalates `NO_NEW_RISK`; restart reads that state from the order receipt. Set `AI_QUANT_PAPER_DB` to retain paper order receipts, manually reported simulated fills, safety state, control receipts, and audit events across restarts.
- The API requires an operator token for all commands and never exposes a broker route other than `PAPER`.
- `NO_NEW_RISK` paper recovery requires a separately configured `AI_QUANT_RECOVERY_TOKEN`, both token headers, an idempotency key and reason, no unresolved paper orders, and matching local and simulated broker fill ledgers. `FLATTEN_ONLY` and `HALT` cannot be lowered through this API.

The local data module normalizes UTC bars, detects conflicting duplicates, and replays a fixed dataset deterministically. A simple paper backtest applies fees and slippage on the next bar; NautilusTrader integration is still pending. The authenticated `/api/v1/paper/fills` endpoint records **manual simulated fills** directly in the local portfolio. `/api/v1/paper/broker-fills` records a fill in the durable paper simulator, and `/api/v1/paper/orders/{order_id}/sync-fills` imports its fills into the portfolio atomically. `/api/v1/paper/positions/reconcile` compares local and simulator fill IDs, net base units, and quote-currency cash flow separately for every instrument. Its version 1.1 response contains an instruments list; legacy scalar fields explicitly summarize EURUSD. A mismatch persists `NO_NEW_RISK` and requires reconciliation. The backend `/api/v1/portfolio/snapshot` defaults to EURUSD and accepts an `instrument` query parameter; `/api/v1/portfolio/positions` reports all configured positions with their base and quote currencies; a missing or stale quote leaves marked values unavailable. Paper `desired_exposure` is interpreted as the selected instrument's base units, and fill fees are in its quote currency. `/api/v1/instruments` exposes the fixed paper catalog. `/api/v1/risk?instrument=GBPUSD` selects the corresponding unit ledger. These values are not broker account equity.

For historical bars, install `.[data]` and use `ai-quant-data ingest <csv> --root ./data-local --source-id <source>`. The archive preserves exact CSV bytes, writes a typed Parquet copy, records lineage and duplicate counts, and verifies checksums on replay. `ai-quant-data health <archive-id> --root ./data-local --max-age-seconds 300` reports freshness. See [data/HANDOFF.md](data/HANDOFF.md) for the object layout and migration status.

`GET /api/v1/portfolio/currencies` reports the filled currency balances and the possible balances after pending paper orders. It includes quote-currency fees and each pending intent's slippage allowance, and uses fresh bid/ask conversions to USD. Missing or stale prices leave aggregate values unavailable. Mission Control shows these bounds on the portfolio page. The underlying engine supports explicit metadata for multiple instruments and direct or inverse conversion pairs; the API supports four simulated pairs, with shared currency limits enforced during approval, reservation and pending dispatch. See [the currency valuation decision](docs/adr/0003-currency-ledger-valuation.md).

Quotes and issued risk decisions remain in memory. Safety state, control and recovery receipts, audit events, order receipts, simulated broker acknowledgements, and fills persist when `AI_QUANT_PAPER_DB` is configured. A `PENDING` order means dispatch never began; retry the original intent and idempotency key with a fresh paper quote to recover it. An expired pending order becomes `REJECTED` and releases its reservation. A `DISPATCHING` or `UNKNOWN` order cannot be resent automatically. An authenticated `POST /api/v1/paper/orders/{order_id}/reconcile` checks the durable simulator acknowledgement and resolves a known outcome without dispatch; a missing acknowledgement leaves exposure reserved. Uncertain order escalation is persisted and reconciliation does not automatically lower `NO_NEW_RISK`. After resolving all uncertain orders and fill mismatches, the operator can POST `/api/v1/control/recover-paper` with `X-Operator-Token`, `X-Recovery-Token`, `Idempotency-Key`, and a JSON `reason`. This paper recovery is disabled unless the second token is configured and distinct. Broker sourced fills and live reconciliation remain unimplemented. This is a development slice, not a shadow or restricted-live release. The remaining work and phase gates are listed in [docs/BUILD_STATUS.md](docs/BUILD_STATUS.md).

The local audit history is chained and verified at startup, read and append. `GET /api/v1/audit/integrity` reports validity, event count, head hash and the number of legacy events baselined at upgrade. An integrity failure blocks authenticated commands and audit-history reads. Mission Control displays the result. This does not replace independent immutable audit storage; see [ADR 0006](docs/adr/0006-local-audit-integrity.md) for migration, limits and rollback.

`GET /api/v1/world-state` publishes versioned advisory FX evidence from the paper quotes and recent canonical bars. Authenticated `POST /api/v1/paper/world-state/bars` accepts bounded OHLCV batches; history is in memory and must be reloaded after restart. Mission Control shows source freshness, deterministic regime features and currency coverage. Macro sources remain unconnected. See [ADR 0007](docs/adr/0007-paper-world-state.md) for thresholds, expiry and limitations.

Authenticated `POST /api/v1/paper/decisions/classify` accepts `instrument_id` and `task_id` and emits an expiring advisory regime envelope. The default quantitative fallback requests deep review or waits for evidence. Low confidence and unverified calibration require escalation. The output cannot authorize an order. See [ADR 0008](docs/adr/0008-advisory-decision-gateway.md).

The optional TypeSafe Jev adapter is disabled by default. To enable it, configure `AI_QUANT_JEV_ENABLED=true`, a server-only `AI_QUANT_JEV_API_KEY`, and a pinned `AI_QUANT_JEV_MODEL` version. Moving aliases are rejected. `GET /api/v1/decisions/provider` reports sanitized usage, request-budget and circuit status. Startup makes no provider calls; authenticated classification can send paper market features to the official TypeSafe endpoint when enabled. The adapter has been verified with mock transport only. See [ADR 0010](docs/adr/0010-jev-provider-adapter.md) for bounds, probability semantics, configuration and pending live-provider verification.

Classification responses also include a typed `deep_review` envelope. The dispatcher invokes a configured reviewer for DEEP_REVIEW and reports WAIT_FOR_DATA when more evidence is needed. No reviewer is configured by default. Failed, busy, invalid or timed-out reviews leave escalation pending; completed reviews also remain advisory. Expiry is checked again after audit storage. See [ADR 0012](docs/adr/0012-deep-review-dispatch.md) for the contract.

The optional OpenAI Responses reviewer requires `AI_QUANT_REVIEW_ENABLED=true`, server-only `AI_QUANT_REVIEW_API_KEY` and an explicit dated GPT snapshot in `AI_QUANT_REVIEW_MODEL`. It sends compact paper market evidence with no tools enabled and response storage disabled. Request limits, token accounting and circuit status appear under `deep_review` in the provider status endpoint. Verification has used mock responses only; credentialed verification, model evaluation and durable monetary governance remain open. See [ADR 0014](docs/adr/0014-openai-advisory-review-adapter.md).

New classification results and their audit entries commit together. `GET /api/v1/agents/runs/{trace_id}` retrieves the fast decision and deep review after restart when SQLite is configured. Reads verify the audit binding and archived snapshot provenance. Historical retrieval never calls a provider or extends expiry. See [ADR 0013](docs/adr/0013-durable-advisory-agent-runs.md) for capacity, migration and retention limits.

`GET /api/v1/agents/registry` lists the execution pack's 18 agent identities, exact manifests, required inputs and tool ceilings. Original version 0.1.0 manifests remain PLANNED. A02 has an additional 0.2.0 PARTIAL/SHADOW mission that runs multi-window paper regime analysis; event/crisis sources and calibration remain unavailable. The tool SDK denies unknown/live/control capabilities, limits quarantined/degraded roles, and invokes only trusted installed handlers. Complete missions and promotion remain unfinished. See [the runtime handoff](docs/AGENT_RUNTIME_HANDOFF.md) and [A02 handoff](docs/A02_HANDOFF.md).

Authenticated `POST /api/v1/paper/agents/context` accepts `agent_id`, `agent_version` and `task_id`. It assembles permitted server-owned paper evidence with references, source expiry and explicit missing/denied inputs. Clients cannot send evidence or dataset labels. No model or order is invoked. Context and source byte limits, timeouts and current permissions are enforced; contexts always remain advisory. See [ADR 0016](docs/adr/0016-agent-context-compilation.md).

A07 0.2.0 provides local paper-feed integrity analysis through the same context/execution endpoints. It reports unusable evidence, advisory quarantine flags, bounded process-local ingestion counters and a persisted incident recommendation. Clock measurement and independent lineage remain unavailable; continuous mission scheduling and incident delivery are unfinished. See [A07 handoff](docs/A07_HANDOFF.md).

Context creation also stores the full context and its exact manifest atomically with audit. Operator-authenticated `GET /api/v1/agents/contexts/{context_ref}` retrieves verified historical checkpoints, preserving their original expiry. See [ADR 0017](docs/adr/0017-durable-context-checkpoints.md).

With `AI_QUANT_PAPER_DB` configured, a local LangGraph supervisor stores graph checkpoints and audit-bound invocation reservations in a sibling `.agents.sqlite3` file. Operator-authenticated `POST /api/v1/paper/agents/execute` accepts `run_id` and an archived `context_ref`; `GET /api/v1/agents/graph-runs/{run_id}` reads its receipt, and `POST /api/v1/paper/agents/graph-runs/{run_id}/resume` resumes only uninvoked work after fresh permission/expiry checks. Interrupted calls become UNCERTAIN without automatic replay. A02 and A07 0.2.0 are installed for shadow analysis; other versions report missing evidence or unavailable implementation. Receipts preserve source expiry and always require escalation. See [ADR 0018](docs/adr/0018-langgraph-advisory-runtime.md), [A02 decision](docs/adr/0019-a02-shadow-regime-mission.md) and [A07 handoff](docs/A07_HANDOFF.md).

Authenticated `POST /api/v1/paper/world-state/publish` preserves the current advisory snapshot. Classification also archives its input before provider dispatch and links it in audit history. `GET /api/v1/world-state/archive/{world_state_version}` retrieves verified historical evidence with its original expiry. With `AI_QUANT_PAPER_DB`, archives survive restart; they never restore fresh quotes or issue approvals. Capacity is bounded and new publication fails when full. See [ADR 0011](docs/adr/0011-local-world-state-publication.md).

Classifier evaluation is available through `python -m ai_quant_os.evaluation_cli report observations.json --output calibration-report.json` and the authenticated `/api/v1/paper/decisions/calibration` endpoint. Reports remain evidence only and do not clear escalation or authorize orders. The provider benchmark harness reuses gateway validation and keeps held-out labels out of classifier inputs. See [the evaluation handoff](docs/CALIBRATION_HANDOFF.md) and [ADR 0009](docs/adr/0009-classifier-evaluation-evidence.md).

Built-in backtests now include validated configuration and immutable source provenance. See [strategy provenance handoff](docs/STRATEGY_PROVENANCE_HANDOFF.md) for registry retention and parameterized runs.

`ai-quant-portfolio-backtest` runs a synchronized currency basket in one native USD account with shared collateral checks, actual USD fees and reconciled currency marks. See [native portfolio handoff](docs/NATIVE_PORTFOLIO_HANDOFF.md) for the retained example and remaining limits.

`moving-average-directional` adds native short positions and complete bar reversals with a separate source binding and replay model. It is connected to research 1.0, candidate comparisons and forward shadow diagnostics. See [directional handoff](docs/DIRECTIONAL_BACKTEST_HANDOFF.md) for commands, retained examples and pending statistical policies.

## Local research validation

The archived-data research runner performs native walk-forward, held-out and cost-stress evaluation with immutable experiment lineage. Reports retain pending gates and cannot authorize promotion. See [research handoff](docs/RESEARCH_HANDOFF.md) for the synthetic verification example and remaining P08 work.

Research version 1.1 can declare a seeded native closed-trade Monte Carlo screen before execution. See [Monte Carlo handoff](docs/MONTE_CARLO_HANDOFF.md) for policy, currency units, sample requirements and preserved legacy archives.

Research version 1.2 adds declared native missing-observation and downward-step screens with archived cash-flow reconciliation. See [adversarial handoff](docs/ADVERSARIAL_HANDOFF.md) for usage and remaining coverage.

Directional research can explicitly declare equal-reversal fee attribution for Monte Carlo and use those same adverse-market screens. See [directional research handoff](docs/DIRECTIONAL_RESEARCH_HANDOFF.md) for the policy and retained synthetic evidence.

Completed experiments can be registered and exported through `ai-quant-candidates` and protected local API routes. See [candidate registry handoff](docs/CANDIDATE_REGISTRY_HANDOFF.md) for source retention, withdrawal and pending promotion gates.

The paper API now exposes reconciled cumulative strategy contributions from persisted ownership and fills. See [paper attribution handoff](docs/PAPER_ATTRIBUTION_HANDOFF.md) for marking, exact fee accounting and remaining P09 evidence.

New paper orders retain their initial approved quotes before dispatch. A protected execution-evidence API reconciles midpoint, spread, decision-to-fill and fee components; see [execution attribution handoff](docs/EXECUTION_ATTRIBUTION_HANDOFF.md).

`ai-quant-counterfactual` compares declared size, cost and parameter alternatives with a frozen candidate's recorded holdout result in separate native engines. Reports remain diagnostics on already-seen data with no promotion authority. See [counterfactual handoff](docs/COUNTERFACTUAL_HANDOFF.md).

`ai-quant-shadow` assigns a candidate to an isolated diagnostic portfolio and appends post-assignment observations with cumulative native fill/position/PnL evidence. Restart, sequential reservations, withdrawal refusal and historical fill-prefix checks are durable. See [shadow portfolio handoff](docs/SHADOW_PORTFOLIO_HANDOFF.md) for commands and remaining production integration.

Protected paper evidence routes now capture immutable ledger/quote checkpoints and publish UTC daily cash-plus-inventory reports with retained opening positions and explicit missing marks. See [daily evidence handoff](docs/DAILY_EVIDENCE_HANDOFF.md).

With persistent paper storage and operator configuration, `AI_QUANT_DAILY_EVIDENCE_ENABLED=1` enables a local UTC boundary capture/publication worker. Verified status records published, missed and failed days without backfilling prices. See [daily scheduler handoff](docs/DAILY_SCHEDULER_HANDOFF.md).
