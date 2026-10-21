# Audit fixes handoff — 2026-10-21

This increment closes the code-level gaps found by the V3.2 blueprint
audit of this repository (repo HEAD e69cbd8). It does **not** change any
release gate: P00–P12 remain in progress, P13 remains blocked, and the
go-live decision remains **NO-GO**. Paper-only authority is unchanged.

## Fixed

1. **Repository hygiene** — `.task-temp/` (43 tracked files: pytest temp
   data, browser state, SQLite `-wal`/`-shm` files, logs, blueprint
   copies) removed from tracking; `.gitignore` now covers `.task-temp/`,
   `*.sqlite3-wal`, `*.sqlite3-shm`, `*.sqlite3-journal`, `.venv-test/`.
2. **Six-rung safety ladder (V3.2 §28)** — `SafetyState` is now
   NORMAL → CAUTION → REDUCED (`REDUCED_RISK` on the wire, matching the
   Mission Control operator console) → NO_NEW_RISK → FLATTEN_ONLY → HALT.
   CAUTION permits new risk; REDUCED refuses it; gated paper recovery now
   accepts CAUTION/REDUCED/NO_NEW_RISK and still refuses HALT/FLATTEN_ONLY.
   Runtime literals, published JSON schemas (9 files), and the web
   risk/realtime models were updated to match. New tests:
   `tests/test_safety_states.py`.
3. **Versioned risk policy (V3.2 §28/§34)** — the paper ceilings that
   were literals in `api.py` now live in `risk_policy.py` as
   `PaperRiskPolicy` (`paper-risk-1.0.0`, SHA-256 digest, fail-closed
   loading from `AI_QUANT_RISK_POLICY_PATH`). Defaults are identical to
   the old literals, so behaviour is unchanged without a policy file.
   Example: `data/risk_policy.paper.v1.json`. Tests:
   `tests/test_risk_policy.py`.
4. **Architecture declaration** — `.clean/architecture.md` now carries
   the machine-readable fenced block external boundary checkers require,
   generated from the canonical `.clean/architecture.json`. All new
   modules below are registered in the inventory, a primary layer, and
   the criticality map (risk_policy = critical, the rest supporting).
5. **Own MCP tool fabric (V3.2 §11)** — `mcp_tools.py`: 27 typed tools in
   the 9 blueprint families, per-agent allow-lists, approval gating,
   JSON-RPC `tools/list` / `tools/call`. No broker-order or credential
   tool exists and write tools in Code / Shadow families are
   sandbox-only by construction. Tests: `tests/test_mcp_tools.py`.
6. **Agent councils (V3.2 §7)** — `councils.py`: the 6 blueprint
   councils and the PROPOSER → CHALLENGER → VERIFIER → POLICY →
   (HUMAN) protocol with hard-fail veto and escalation.
   Tests: `tests/test_councils.py`.
7. **AI Compute & Cost Governor core (V3.2 §15)** — `cost_governor.py`:
   model-tier routing (DETERMINISTIC/ROUTINE/DEEP/FRONTIER with
   escalation flags), platform→squad→agent→task budget hierarchy,
   ring-fenced emergency reserve that never denies incident/safety work,
   token-storm detector (velocity + handoff loops), prompt-cache keys,
   cost/value scoring. Tests: `tests/test_cost_governor.py`.
8. **Independent Recovery Verifier (V3.2 §13)** — `recovery_verifier.py`:
   the 8-gate recovery quorum and the freeze → quarantine → repair →
   replay → canary → RECOVERY_VERIFIED → REDUCED_RISK → NORMAL state
   machine. No frozen→NORMAL jump exists; a repair agent's
   self-assessment alone is rejected (independent replay is mandatory).
   Tests: `tests/test_recovery_verifier.py`.
9. **Currency twins (V3.2 §17)** — `currency_twin.py`: 9-component twin
   (8 content components + confidence), staleness-penalised confidence,
   and the PAIR_SCORE formula that returns 0 below the confidence floor.
   Tests: `tests/test_currency_twin.py`.
10. **INR instruments declared (V3.2 §37)** — `inr_instruments.py`:
    USDINR/EURINR/GBPINR/JPYINR catalog with USD conversion paths,
    status `PENDING_DATA`. Deliberately **not** merged into
    `PAPER_INSTRUMENTS` yet (see Open below).

## Still open — not solved by this increment, and not claimed as solved

- **Items 5–9 are standalone, tested modules.** They are not yet wired
  into the live paper path (`api.py` composition root) or Mission
  Control. Wiring each one is a separate gated increment with its own
  acceptance evidence — the same discipline the rest of the repo uses.
- **INR activation** needs (a) an INR reference-rate feed for the
  USD-reporting currency-risk conversion and (b) a deliberate catalog
  change across the fixed paper schemas/tests. Declaring the catalog
  was the honest first step; inventing rates would have been worse.
- **Rust risk/execution services, AWS/Terraform (Mumbai/Hyderabad),
  live broker adapters, OIDC/SSO+MFA, paid data licences** — these need
  infrastructure, accounts, approvals and money. No code in this
  increment pretends otherwise.
- **The 18 agents remain PLANNED/PARTIAL.** Nothing in this increment
  promotes an agent to IMPLEMENTED; that status is earned per-mission.
- **Profitability** still depends on a validated strategy edge, which
  no platform code creates. See the profitability roadmap work.

## Verification

- New tests (safety states, risk policy, MCP, councils, cost governor,
  recovery verifier, currency twin, INR) plus the architecture boundary
  suite: **145 passed together**.
- Affected existing suites (trading path, operator controls/rejections,
  paper recovery, health watchdog, incident, realtime, meta allocator,
  currency risk, API): 152 passed; the 3 schema-parity failures caused
  by the new safety states were fixed (see item 2) and re-verified
  green (62 passed in that group). Two failures remain that also fail
  on the **unmodified tree** in this Linux sandbox (published-schema
  parity under a different Pydantic version than the repo pins, and the
  realtime HTTP stream test) — recorded here so they are not mistaken
  for regressions from this increment.
- Web: 192/192 non-visual tests pass, ESLint clean, TypeScript clean
  (`tsc --noEmit --incremental false`). The visual suite and
  `next build` could not complete in the audit sandbox (EPERM writing
  build/temp files — an environment restriction, not a code error).
- A full-suite run in the audit sandbox is **not** claimed: the sandbox
  began refusing Python file creation in temp directories mid-run
  (`tempfile` finds no usable temp dir; the unmodified tree fails
  identically within seconds). The authoritative full-suite check for
  this increment is the repo's own GitHub Actions CI on the branch.
- Ruff check and format pass on all new/changed Python files.

## Phase 2 — wiring the standalone modules into the paper path (2026-10-21)

This increment wires the Phase 1 standalone modules into `api.py`'s
paper path. Safety semantics are unchanged: the safety ladder,
recovery accept/refuse logic and risk-policy defaults were not
touched. All new HTTP behaviour fails closed.

### What was wired

1. **MCP HTTP surface** (`src/ai_quant_os/mcp_api.py`, new).
   `GET /api/v1/mcp/tools` and `POST /api/v1/mcp/tools/call`, both
   behind the existing `require_operator` dependency, installed from
   `create_app` next to the agent-call routes. `create_app` gained an
   optional `mcp_agent_tools` allow-list mapping kwarg. Default
   allow-lists expose read-scope tools only (`operator`, or agent ids
   `A01`–`A18`); unknown agents are refused with 403. Registry errors
   map like the rest of the paper API: `PermissionError` → 403,
   `LookupError` → 409, `ValueError` → 422. Approval-gated tools are
   refused without approval exactly as the registry dictates.
   **Only one handler is registered in the paper path:**
   `get_risk_state` (returns the current safety state with
   `"mode": "PAPER"`). Every other catalogued tool fails closed with
   409 (no handler) until a tool-specific wiring increment registers
   it. No broker-order or credential tool exists in the registry and
   none was added.
2. **Cost accounting on the MCP path** (`src/ai_quant_os/mcp_costs.py`,
   new). A thin adapter over `cost_governor.BudgetHierarchy` — the
   governor core was not edited. Each tool call that declares
   `estimated_tokens` is modelled as a `material` LLM task in the
   `mcp-tools` squad; admission is checked **before** registry
   dispatch, a non-ADMIT verdict raises `CostCapRefusal`
   (`PermissionError` → 403), and usage is recorded only after a
   successful call. The per-task cap is discovered by probing
   `admit()` (the governor exposes no public getter; no private state
   is read) — 250,000 tokens with the default hierarchy.
3. **Strategy promotion gate** (`src/ai_quant_os/strategy_promotion.py`,
   new). The existing promotion contracts deliberately grant no
   authority (`PromotionDecision.approved_by_policy` is
   `Literal[False]`; `CandidatePaperAssignment.promotion_allowed` is
   `Literal[False]`), so there is **no lifecycle hook to attach to** —
   the gate is delivered as an encapsulated module and lifecycle
   integration awaits the lifecycle owner's hook.
   `evaluate_promotion` requires BOTH (a) a Validation Fortress PASS
   verdict bound to the same hypothesis id and (b) an ACCEPTED verdict
   from the councils.py **research** council (councils.py defines no
   council literally named "strategy"; the research council holds the
   hypothesis / strategy-designer / statistician / skeptic roles).
   The council is always consulted, even when the fortress leg fails,
   so both signals are recorded. Missing verdict, id mismatch, FAIL,
   REJECTED and ESCALATED verdicts all block (fail closed).
4. **Currency-twin signal into the meta allocator**
   (`meta_allocator_sources.py`, extended). New optional
   `allocator_sources_with_twin(...)`: with both twin legs absent it
   returns `allocator_sources(...)` byte-identically (zero
   contribution, existing behaviour untouched); with both legs present
   it adds a `currency_twin` EvidenceRecord whose payload carries
   `pair_score` clamped to [-1, 1]. Exactly-one-leg and
   currency-mismatch inputs raise `ValueError`. The twin's
   confidence floor applies: below the floor the contributed score is
   exactly 0. **Deviation, recorded deliberately:** the source binding
   uses the existing `WORLD_STATE_READ` tool (the same binding
   `currency_strength_sources.py` already uses for its currency-twins
   input) because `CURRENCY_TWIN_READ` is not in `READ_TOOLS` in
   `agent_permissions.py` and bindings reject unregistered tools.
   Registering it is a permissions-owner change and was not slipped
   in here.
5. **Recovery Verifier in shadow mode** (`api.py`, additive only).
   The paper recovery endpoint now computes a
   `recovery_verifier.evaluate_recovery` verdict and stores it on the
   recovery receipt as `recovery_verifier_shadow` — a compact JSON
   string `{"mode":"SHADOW","verdict":…,"failed_gates":[…],
   "evidence_refs":[…]}`. Accept/refuse behaviour is byte-for-byte
   unchanged; the shadow is computed after the accept decision's
   preconditions and rides the stored receipt, so idempotent replays
   return it unchanged. The gate evidence is honest about what the
   endpoint actually holds at that point (completed triage = audit
   evidence completeness, agent-health worker presence); the remaining
   quorum gates are absent, so shadow verdicts are deterministically
   REJECTED naming `independent_replay` until the blueprint §13
   quorum is wired end to end. That is the expected shadow posture,
   not a defect.

### Architecture registration

`mcp_api` (api layer), `mcp_costs` and `strategy_promotion`
(application layer) are registered in `.clean/architecture.json`
(369 modules total) and the machine-readable block in
`.clean/architecture.md` was regenerated from the JSON; the
architecture boundary suite passes (10 tests).

### What remains shadow / off / not wired

- **Promotion gate is unconsumed.** No lifecycle code calls
  `evaluate_promotion` yet; promotion still cannot happen (the
  contracts' `Literal[False]` fields stand). Wiring it in is the
  lifecycle owner's increment.
- **MCP executes one tool.** Only `get_risk_state` has a handler in
  the paper composition root; all other tools list per allow-list but
  refuse calls with 409. Registering handlers (especially any
  approval-tier write tool) is a separate gated increment per tool.
- **Twin source is published, not consumed.** `allocator_sources_with_twin`
  emits the `currency_twin` evidence record, but A13's context still
  compiles only from `INPUT_NAMES`; no allocator weighting reads the
  twin signal yet.
- **Recovery verifier is shadow-only.** Verdicts are recorded, never
  enforced; the §13 8-gate quorum is not wired end to end (see the
  deterministic REJECTED note above).
- Everything in the Phase 1 "Still open" list above remains open
  (INR activation, Rust services, cloud, live brokers, agent status).

### Verification (this increment)

- New tests: `test_mcp_api.py` 11, `test_strategy_promotion.py` 11,
  `test_meta_allocator_twin.py` 9, `test_recovery_verifier_shadow.py`
  6 — all pass. Module suites: MCP tools 16, councils 19, cost
  governor 21, recovery verifier 13, currency twin 40, validation
  fortress 14, meta-allocator agent 4, safety states 5, architecture
  boundary 10 — all pass (179 passed across these files, 0 failed).
- Existing API suites were run in the sandbox: every non-tempfile
  test passes (e.g. `test_api.py` 12 passed, operator controls 10,
  rejections 2, pending 1, health 1, recovery API 1). The remaining
  results are **environment errors, not regressions**: the sandbox's
  intermittent write guard makes `tempfile` report "No usable
  temporary directory", erroring every `tmp_path`-fixture test at
  setup (test_api 15, operator controls 3, meta-allocator API 3,
  promotion store 4, incident/paper-recovery/risk-policy batches
  similarly) on unmodified files identically. `test_api.py`'s one
  failure is the Phase 1-documented Pydantic-version schema-parity
  failure, also present on the unmodified tree. `test_promotion_contracts.py`
  cannot collect in the sandbox venv (no `jsonschema` package); with
  the package staged it passes 6/6.
  The authoritative full-suite check remains the repo's GitHub
  Actions CI.
- Ruff check passes repo-wide (`src`, `tests`); Ruff format passes on
  all Phase 2 new/changed files. Note for the record: mid-increment,
  the sandbox write guard corrupted several new files during an
  in-place formatter run; all were recreated through the safe write
  channel and re-verified by size and test counts, and formatting was
  finished via a stdin/stdout pass. No source content was lost.

## Phase 3 — completing the Phase 2 wiring (2026-10-21)

This increment executes the four wirings Phase 2 deliberately left as
stubs/shadow. Safety semantics are unchanged except where stated, and
risk-policy defaults are unchanged. Nothing below promotes an agent,
opens a release gate, or changes the go-live **NO-GO**.

### What is now wired end to end

1. **MCP per-tool handlers** (`src/ai_quant_os/mcp_handlers.py`, new;
   `mcp_api.py` extended; bound in `api.py`'s `create_app`).
   `paper_mcp_service` now takes an optional `McpToolBindings`; a
   handler is registered only when its real backing source is bound.
   With `bindings=None` the service behaves exactly as Phase 2.
   `create_app` binds the composition-root sources, so on the live
   paper path these tools now execute honestly: `get_risk_state`
   (Phase 2), `query_dataset` (ResearchEvidenceCatalog),
   `get_exposure` (execution gateway snapshot + real fills),
   `simulate_allocation` and `stress_portfolio` (pure computations
   over real fills, labelled non-mutating), `simulate_risk_change`
   (pure, over the real kernel state; never applies),
   `get_broker_health` (BrokerRouter snapshot/eligibility),
   `get_incident` (HealthStore), `get_policy` (the versioned
   `PaperRiskPolicy`, served only by its exact version id, with
   digest), `export_evidence` (PaperStateStore chain-validated audit
   read). `mcp_handlers` imports stdlib only — bindings are
   duck-typed, so the fabric structurally cannot reach broker/order
   code. The registry's approval gating is untouched.
2. **Twin weighting** (`risk_policy.py`, `meta_allocator_sources.py`).
   `PaperRiskPolicy` gains one optional field,
   `twin_evidence_weight` (Decimal, default `0`, validated to
   [0, 1]); the JSON loader keeps the eight original fields required,
   defaults this one, and still fails closed on unknown fields or bad
   values. `allocator_sources_with_twin(..., risk_policy=...)` now
   publishes a `WeightedTwinPairObservation` whose
   `weighted_pair_score = clamp(weight × pair_score, −weight,
   +weight)` via `apply_twin_weight` — the weight can never widen the
   signal, and the confidence floor still zeroes `pair_score` first.
   With no policy supplied the twin payload and `source_id` are
   byte-for-byte Phase 2; with weight 0 the weighted score is exactly
   0. The example policy file is unmodified.
3. **Recovery quorum evidence production**
   (`src/ai_quant_os/recovery_evidence.py`, new; `api.py` recovery
   region rewired). The shadow closure now calls
   `produce_recovery_evidence`, which computes every gate live from
   the paper system in the same call, each section independently
   fail-closed: `agent_health` (health store present, no open
   incidents), `data_state` (completed triage + audit integrity +
   snapshot hash), `repair_verification` (only from verified
   completed-triage artifacts — a plain operator stop with no triage
   artifact honestly yields False), `risk_execution` (kernel state ==
   store state, kernel/policy limit digests equal), `broker_truth`
   (no unresolved orders and `compare_paper_positions` matches,
   computed before any mutation), `security` (explicit endpoint
   attestation — the handler sits behind both the control-operator
   and recovery dependencies, whose tokens app construction forces
   to differ), `resume_controller` (narrow structural sense only:
   the verdict is computed synchronously by the deterministic
   endpoint). `independent_replay` is a real replay: the paper
   SQLite DB is copied via the backup API under the store lock and
   re-opened through the store classes on the copy (never the live
   connection); audit head/count, safety state and ledger digest must
   all match live. The endpoint's accept/refuse logic, ordering and
   receipt shape are unchanged; any producer exception degrades to
   the Phase 2 all-gates-failed shadow posture, so evidence
   production can never change a recovery outcome.
4. **Promotion lifecycle consumer**
   (`src/ai_quant_os/promotion_lifecycle.py`, new;
   `promotion_contracts.py` gated). `PromotionDecision` no longer
   pins approval to `Literal[False]`: `approved_by_policy` is a real
   bool and `decision` may be `WAIT_FOR_EVIDENCE` / `PROMOTED` /
   `REJECTED`, with new optional gate-evidence fields
   (`fortress_hypothesis_id`, `fortress_verdict_ref`,
   `council_name`, `council_verdict`, `decided_at`). A model
   validator makes approval true **iff** the record is PROMOTED with
   complete gate evidence (fortress ref + research-council ACCEPTED +
   timestamps); every other approval claim raises. Legacy stored
   records still validate, so `PromotionEvidenceStore` re-verification
   is unaffected. `docs/schemas/promotion-decision-v1.schema.json`
   was regenerated from the model (dict-identical; parity test
   compares dicts). `request_promotion` derives the fortress verdict
   ref as a SHA-256 content hash (the verdict type has no id), calls
   `strategy_promotion.evaluate_promotion` (never reimplements it),
   records a `PromotionDecision` in the evidence store — the fortress
   leg honestly fills `offline_eval`; the other four evaluations stay
   `NOT_RUN` with the reason "not part of the fortress+council
   promotion gate" — and flips the strategy to `PAPER_ACTIVE` in the
   new audited `PromotionRegistry` only when the gate returns
   promoted. Registry status is derived from recorded attempts, and
   repeating a request with identical gate evidence is idempotent
   (same outcome, no duplicate attempt or flip).

### What remains stubbed / shadow — and precisely why

- **Recovery verifier stays SHADOW.** The `canary` gate is not
  honestly producible on this path: paper recovery lands directly at
  NORMAL (`RiskKernel.recover_paper_safety`), and no staged
  REDUCED_RISK canary phase exists. Producing a canary would require
  changing where recovery lands — a safety-semantics change outside
  this increment. In a healthy on-disk deployment every other gate
  and the independent replay now pass, so the shadow verdict is
  REJECTED with `failed_gates == ["canary"]`; on the in-memory app
  (no health watchdog, no triage artifact, no on-disk DB) the honest
  failed set is `["agent_health", "repair_verification", "canary",
  "independent_replay"]`. Enforcement awaits a staged-landing
  recovery design owned by the safety lifecycle.
- **14 MCP tools stay 409** (no handler in the default app):
  `create_hypothesis` and `build_strategy_artifact` (creation sits
  behind compiled-context/graph custody; a direct store write would
  bypass it), `create_experiment`, `run_backtest`, `compare_runs`,
  `run_walk_forward`, `leakage_scan`, `stress_regime`,
  `capacity_test` (candidate-validation legs that need the full
  validation bundle / isolated worker; the Monte Carlo engine
  specifically has no by-candidate lookup, and running it over paper
  fills would misattribute paper trades to a candidate),
  `create_branch`, `run_tests` (isolated worker custody only),
  `create_approval_request` (no generic approval-request store
  exists), `replay_session` (needs a full candidate dataset package,
  no session store). `deploy_shadow`, `pause_shadow` and
  `compare_challenger` have handler *seams* (optional callables on
  the bindings, approval gating fully in force) but are deliberately
  unbound in `create_app`: no shadow deployment service exists in
  the default composition root, so they stay 409 there.
- **`CandidatePaperAssignment.promotion_allowed` stays
  `Literal[False]`** — deliberately not widened. It is a frozen
  challenger-admission binding whose `authorize()` keys off
  CHALLENGER state/revision/fingerprints; giving stored assignments
  promotion authority would silently change trade-authorization
  semantics. Promotion authority lives in the gated
  `PromotionDecision` + registry `PAPER_ACTIVE` status above.
- **Twin weighting has no live consumer yet** — the same position as
  Phase 2, one level up: the weighted signal is computed and
  published on the twin observation, but `api.py`'s A13 context path
  still compiles only from `INPUT_NAMES` and no twin feed exists in
  the live path to bind. The policy plumbing is complete so a future
  twin feed lands on a finished gate.
- Everything in the Phase 1/2 "still open" lists remains open (INR
  activation, Rust services, cloud, live brokers, agent status,
  release gates).

### Architecture registration

`mcp_handlers`, `recovery_evidence` and `promotion_lifecycle`
(application layer, supporting) are registered in
`.clean/architecture.json` (372 modules total) and the
machine-readable block in `.clean/architecture.md` was regenerated
to match (verified byte-identical to the generator output); the
architecture boundary suite passes (10 tests).

### Verification (this increment)

- Integrated runnable suites: **169 passed, 0 failed** (`test_mcp_api`
  11, `test_mcp_tools` 16, `test_strategy_promotion` 11,
  `test_councils` 19, `test_cost_governor` 21,
  `test_meta_allocator_twin` 9, `test_meta_allocator_agent` 4,
  `test_currency_twin` 40, `test_recovery_verifier` 13,
  `test_recovery_verifier_shadow` 6, `test_safety_states` 5,
  `test_validation_fortress` 14) — plus the architecture boundary
  suite 10/10. A live `create_app` smoke over HTTP confirms the new
  bindings end to end: `get_policy` returns the real policy + digest
  (unknown id → 409), `get_exposure` returns the real snapshot,
  `deploy_shadow` is 403 unapproved and 409 approved (unbound),
  `run_backtest` is 409.
- New Phase 3 tests total 76 (`test_mcp_handlers` 16,
  `test_twin_weighting` 40, `test_recovery_evidence` 8,
  `test_promotion_lifecycle` 12) plus the reworked
  `test_promotion_contracts` (25). Of these, every test that does not
  need on-disk SQLite passed here; the disk-bound remainder
  (11 MCP-handler store tests, 7 twin-weighting file-loader tests,
  7 recovery-evidence on-disk tests, 12 promotion-lifecycle store
  tests, and the pre-existing disk-bound tests in
  `test_promotion_store` / `test_risk_policy`) is blocked **only** by
  the sandbox write guard: all 47 failure/error lines in that batch
  carry exclusively the environment signatures
  (`sqlite3.OperationalError: disk I/O error`,
  `PermissionError: Operation not permitted` on tempfile/write), a
  bare `CREATE TABLE` probe fails identically in every directory,
  and the unmodified `PromotionEvidenceStore` fails construction the
  same way — tree-independent environment failure, not regressions.
  The blocked assertions were additionally validated by equivalent
  in-memory runs of the same store classes (handler smoke: all
  passed; fortress/council/promotion logic: 36 real passes in the
  contract + gate suites). `test_promotion_contracts` cannot collect
  in this venv (no `jsonschema` package — environment); its schema
  parity was re-verified by direct dict comparison
  (published == model: True).
- `test_api.py`: 12 passed / 16 failed — 15 environment (tempfile /
  disk I/O) plus the one Pydantic-version schema-parity failure
  documented on the unmodified tree in Phases 1–2. No Phase 3 file
  touches the trade-intent schema.
- Ruff check passes on all Phase 3 new/changed files; AST parse
  verified for all of them; byte sizes re-verified after every edit
  (no in-place rewriters were used anywhere in this phase).
