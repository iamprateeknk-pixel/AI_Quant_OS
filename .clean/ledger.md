# Active development ledger

## Objective

Continue the user's full build request: complete all pending development in the AI Quant OS execution pack. Do not mark the objective complete from a single phase increment. Use `docs/BUILD_STATUS.md`, the supplied final execution pack, and phase acceptance checklists to identify unfinished work.

## Completed increments

### P09 candidate-bound PAPER assignment

Committed as `e6bba8d`. An operator binds an eligible unchanged challenger to its exact archived bundle and genome, strategy reference, instrument, per-order exposure ceiling, and bounded expiry. Order admission rechecks current candidate lifecycle and assignment state; the paper attribution ledger retains candidate/genome hashes and linked order references. Candidate source code does not generate intents. SQLite writer reservations serialize admission against lifecycle changes.

Bugbot reported no remaining findings. The full suite at that increment passed 2,962 tests with 36 skipped.

### P09 daily PAPER/shadow diagnostic

Current increment: a protected operator API compares observed fill cash and fees for one exact candidate/genome/strategy/instrument and UTC day, using immutable daily execution evidence and two consecutive hash-linked shadow batches. It refuses mismatched intervals and ownership, verifies retained source history, and explicitly grants no causal or promotion authority. See `docs/PAPER_SHADOW_COMPARISON_HANDOFF.md`.

Focused API, comparison, archive-integrity, shadow history, daily evidence, architecture and schema tests pass. Bugbot review reported no remaining actionable findings. The full suite passed 2,965 tests with 36 skipped in 1,064.95 seconds. Ruff and formatting checks passed earlier in the increment; rerun final checks before commit.

### P09 reality-gap alert polling feed

Current increment: a protected no-cache operator endpoint derives `DEMOTION_REQUIRED` alerts from verified retained shadow reports and the immutable assignment policy. It stays readable after restart and returns source hashes and the measured threshold. The API has no external notification or acknowledgement semantics. Twenty focused reality-gap, shadow, and API tests pass. Bugbot found no actionable findings. The full suite passed 2,966 tests with 36 skipped in 1,150.98 seconds; Ruff lint/format checks and BUILD_STATUS.md CP1252/CRLF validation pass.

### P09 daily multi-batch PAPER/shadow comparison

Committed next increment: the protected daily comparison now aggregates all consecutive verified shadow batches appended within the selected UTC day, from the prior pre-day report through the requested final sequence. Source amounts remain server-owned, and the result retains initial/final hashes that commit to the complete chain. A mid-day batch crossing a UTC boundary is refused. The affected shadow/daily/aggregation suites pass (33); the full suite passes (2,971 passed, 36 skipped in 1,902.01 seconds); Ruff and format checks pass; Bugbot found no actionable issues. The structure map reports no misplaced/unreferenced source. The boundary checker cannot parse the repository’s descriptive Markdown declaration, so the changed imports were manually checked; no framework dependency entered the domain module. Rollback is code-only: revert this increment’s route/domain edits; no persisted format or archive changed.

### P09 multi-day PAPER/shadow attribution

Completed increment: a protected no-store range endpoint combines retained daily execution evidence with the verified shadow history for up to 366 days. It reports per-day comparisons and exact quote-currency totals, with distinct missing-day reasons for missing daily evidence, missing shadow observations, absent pre-day baseline, and cross-day batches. No gap is treated as zero activity; causal and promotion authority remain false. The affected shadow/daily suites pass (33); the full suite passes (2,971 passed, 36 skipped in 1,644.20 seconds); Ruff, format and diff checks pass; Bugbot found no actionable issues. The map reports no misplaced or unreferenced source. Rollback is code-only; no persisted archive format changed. See `docs/PAPER_SHADOW_COMPARISON_HANDOFF.md`.

### P09 agent-linked PAPER fill observations

Current increment: a protected no-store report associates exact paper-order fill cash/fees only with a single archived advisory run named by an existing `agent-run:<run_id>` evidence reference. It verifies stored intent hashes, order/fill ownership, run audit integrity and world-state provenance; missing and ambiguous references remain outside totals. The report makes no causal or promotion claim. A12/A13 still do not generate order intents, so full agent contribution attribution remains open. The focused agent/run/paper/execution attribution group passes (48 tests); the full locked Windows suite passes (2,976 passed, 36 skipped in 1,182.55 seconds). Ruff lint/format, architecture inventory and criticality checks, and diff checks pass. Bugbot found no actionable findings. A12/A13 remain advisory and disconnected from order generation, so full causal attribution is still open. See `docs/AGENT_CONTRIBUTION_HANDOFF.md`.

### P09 exact-time demo/shadow position observation

Current increment: a protected no-store API reports direction only for the exact instrument in one verified shadow report and the latest local Exness MT5 demo portfolio record. It only returns position values when the source-bar timestamp and demo portfolio observation timestamp are identical; otherwise it withholds both. Strategy-model units and current-symbol-metadata broker units are separately labeled and never subtracted or equated; magnitude comparison is explicitly unavailable. Hedged positions with net-zero broker quantity are distinguished from flat inventory, and inventory presence remains truthful when quantities are withheld. The response preserves unverified/non-atomic caveats, report hashes and false causal/promotion/order-authority fields. It is not a live execution or PnL comparison, and it does not link broker orders to the candidate. Focused comparison/API/shadow/architecture tests: 68 passed. Full locked suite: 2,981 passed, 36 skipped. Ruff lint/format and both dependency lock profiles pass; Bugbot found no remaining actionable findings. See `docs/DEMO_SHADOW_POSITION_COMPARISON_HANDOFF.md`.

### P09 marked agent contribution report

Current increment: a new protected no-store v2 route adds marked inventory and P&L to the verified agent-run-to-PAPER-fill accounting association. It uses the common liquidation-side mark selected from linked fills per instrument, preserves the mark timestamp and world-state version, withholds marked values for missing/untrusted/future/stale marks while retaining cash/fee totals, and reconciles instrument totals to run rows. Owner-level timing, instrument and archived-run checks are repeated before inclusion; invalid owners and fills remain unattributed. The v1 response contract remains unchanged. Focused agent attribution, paper attribution and archived-run tests pass (36); the full locked Windows suite passes (2,985 passed, 36 skipped in 1,088.47 seconds). Ruff, formatting, both dependency locks and Bugbot review pass. Causal contribution remains unproven because the run-to-intent reference is explicit evidence association, not proof of agent order generation. See `docs/AGENT_CONTRIBUTION_HANDOFF.md`.

### P09 forward-shadow counterfactual replay

Current increment: a protected PAPER endpoint accepts bounded declared alternatives for an exact verified shadow report, reruns the retained configuration as a reproducibility baseline, and archives the immutable report and source in its own SQLite store. No broker, candidate lifecycle, causal claim, or promotion authority is included. Five focused tests pass; the full suite passed 2,971 tests with 36 skipped in 2,120.09 seconds; Ruff and Bugbot checks pass. See `docs/SHADOW_COUNTERFACTUAL_HANDOFF.md`.

### P09 demo/shadow interval coverage

Current increment: the exact-time demo/shadow direction observation now also requires the declared MT5 history interval to cover every source bar in the selected shadow report. If demo history begins after the first shadow bar, the no-store response withholds both quantities and reports `HISTORY_WINDOW_MISMATCH`; the response exposes both source intervals. Focused demo/shadow, shadow archive and MT5 comparison tests pass (60); the full locked Windows suite passes (2,986 passed, 36 skipped in 1,085.55 seconds); Ruff and formatting pass; Bugbot found no actionable findings. This remains a single-instrument final-position direction observation with unverified native history; fill, PnL, financing, ownership and full live-broker comparison remain open. See `docs/DEMO_SHADOW_POSITION_COMPARISON_HANDOFF.md`.

### P09 per-bar demo/shadow position coverage

Current increment: a protected no-store endpoint reconstructs the shadow position direction at each source bar from the verified native shadow fills, then joins only to audited MT5 demo position snapshots captured at that exact timestamp. It binds reads to the account scope and current published projection head, returns every bar with COMPLETE/PARTIAL/NONE coverage, and never forward-fills a missing snapshot. Quantities remain in separate strategy and broker bases; causal, order, portfolio and promotion authority remain false. Focused demo/shadow and projection tests pass (79); Ruff/format and schema parity pass. The full locked Windows suite passes (2,990 passed, 36 skipped in 1,095.63 seconds); Bugbot found no actionable findings. The report covers one selected shadow assignment/instrument and does not reconcile fills or P&L. See `docs/DEMO_SHADOW_POSITION_COMPARISON_HANDOFF.md`.

### P09 durable PAPER market-bar ingress

Current increment: accepted PAPER world-state bars are now retained in the persistent SQLite workspace with canonical row hashes and exact duplicate idempotency. Conflicting instrument/timestamp replacements are refused. Bar rows and their audit-chain event commit atomically; storage/audit errors leave the in-memory world state unchanged and return unavailable. Inclusive instrument/time reads are bounded at 4,096 rows, with one capacity check per batch. Focused world-state and ingress tests pass (36); the full locked Windows suite passes (2,997 passed, 36 skipped in 1,130.63 seconds); Ruff, formatting and diff checks pass. Bugbot found no remaining actionable issues. This retains only operator-submitted PAPER_SIMULATED data: provider/source authenticity, deletion-proof completeness, and automatic shadow scheduling remain open. See `docs/SHADOW_MARKET_INGRESS_HANDOFF.md`.

### P09 automatic local PAPER shadow scheduling

Current increment: an opt-in application-lifespan worker advances verified, non-retired shadow assignments from persisted PAPER bars, with deterministic idempotency keys, an audit-integrity gate, bounded inventory/source reads, capped retry backoff, and a protected no-store status endpoint. It waits for sufficient initial history and never invents missed bars. Assignment enumeration re-verifies stored records. Focused scheduler, API, and store tests plus the full suite and independent Bugbot review are recorded in `docs/BUILD_STATUS.md`. This remains local operator-submitted PAPER_SIMULATED evidence; source authenticity, production recovery, live broker comparison, financing, and P00-P13 release gates remain open. See `docs/SHADOW_SCHEDULER_HANDOFF.md`.

## Remaining P09 work

- Full live-broker-versus-shadow comparison with complete candidate/genome and interval coverage.
- Causal agent contribution attribution; the marked PAPER report covers verified linked fills, but do not infer agent causality because advisory A12/A13 are disconnected from order creation.
- External reality-gap notification delivery and production shadow market ingress/scheduling.
- Financing/restatements and production schedule retention/recovery evidence.
- Financing/restatements, live broker comparison, production shadow ingress/scheduling, agent attribution, and the full P09 acceptance checklist/independent release evidence.

## Broader project

P00-P13 and overall release gates remain incomplete. Continue with the next explicitly listed phase gaps after this P09 increment; preserve paper-only boundaries and do not claim production, promotion, or live-broker evidence without it.

### A16 verified incident context increment

Added A16 0.3.0 while preserving the 0.2.0 contract. The new version projects at most eight verified health incident IDs, codes, timestamps, and agent manifest pins; raw evidence and subjects are omitted. It still blocks on missing trace/test receipts and cannot create or promote repairs. Finish full-suite, lint, format, and diff verification, then continue the remaining P10 and P00-P13 requirements. See ADR 0203.

### A16 incident-bound graph trace increment

Added A16 0.4.0 using the audited graph ledger to recover failed-run traces and pair each trace ID with its health incident ID. Missing runs stay partial, mismatched pins are refused, and code-test receipts remain a hard blocker. Finish full-suite and quality verification before continuing P10 independent verification and the remaining P00-P13 requirements. See ADR 0204.

### P03 native replay streaming contract and P07/A07 ingress telemetry

Added an opt-in `StreamingStrategy` contract for native standard and directional bar replay. Strategies receive one completed bar and decide at the next bar open; existing history-based plugins remain supported. Tick replay rejects the bar-only interface. This bounds plugin-owned history only; the event tape and verifier are still retained, so total process memory is not bounded by this feature. A07 now counts malformed schema submissions on canonical PAPER quote/bar routes without preserving payload contents. Focused A07/API/context tests pass (59); native replay tests pass (46); trading-path/attribution properties pass (69). The latest full backend suite passes (3,217 passed, 43 skipped); repository-wide Ruff, formatting, and diff checks pass. Native MT5, independent acceptance, complete missions, and P00-P13 release gates remain open. See `docs/BUILD_STATUS.md`, `docs/NAUTILUS_HANDOFF.md`, and `docs/A07_HANDOFF.md`.

### P03 verified strategy catalog selection

Added a verified package catalog listing and exact catalog reference selection to the native backtest CLI. The canonical fingerprint binds all sorted versioned implementation manifests and is rechecked at preparation and after the native run. The listing is read-only and validated against `docs/schemas/strategy-catalog-v1.schema.json`; stale catalogs, ambiguous names, unknown versions and incompatible input kinds are refused before input parsing/execution. Registry and native replay suites pass (59); the full suite passes with this increment included (3,219 passed, 43 skipped). Arbitrary archived strategy source remains non-executable; untrusted plugin isolation and full engine memory bounds remain open.

### P03 native replay event budget

Native bar replay now admits at most 100,000 quote events (50,000 bars); recorded-tick replay admits at most 100,000 events. Oversized datasets are rejected before event-tape construction or source copying. The CLI checks its bounded CSV row count before dataset construction; the focused data, registry, bar, directional, and tick replay group passes (75). This bounds input inventory, not total process memory; the latest full backend suite passes (3,223 passed, 43 skipped). Untrusted plugin isolation remains open.

## Rollback

Revert the specific increment commit to remove its API and admission path. The candidate assignment database change is additive; rollback leaves its records inert, and those records must not be deleted as part of code rollback. The daily comparison and reality-gap alert increments read retained evidence and do not rewrite those archives. The multi-batch comparison change has no persisted state; rollback only restores the prior route/domain selection behavior.

### P12 authenticated Mission Control route smoke (2026-10-20)

The production UI and isolated local PAPER API accepted a synthetic operator session, rendered the authenticated Agents, Brokers, Orders, Audit, Costs, Settings, Traces, and Incidents routes, and returned HTTP 200 for all observed UI/API requests with no console errors or warnings. The fresh database remained at NORMAL with no orders or fills, and no safety action or order was submitted. Exact route observations and cleanup are recorded in `docs/evaluations/mission-control-authenticated-route-smoke-2026-10-20.json`. This does not close authenticated accessibility, responsive keyboard, nonempty-state, independent security, or P00-P13 acceptance.

### P12 authenticated accessibility and broker table keyboard navigation (2026-10-20)

Fixed keyboard access for horizontally overflowing tables on Dashboard, Brokers, World State, and Portfolio with labeled, focusable regions and left/right arrow scrolling. The issue was reproduced on Brokers, World State, and Portfolio: axe reported `scrollable-region-focusable` before the fix. Afterward the full web suite passes (201), ESLint passes, and the optimized Next.js build with TypeScript passes. The authenticated production-build scan covered 16 navigation routes, the incident runbook, and an unknown-trace detail at 1280x800, 390x844, and 320x800 (54 checks): axe WCAG 2.1 and 2.2 A/AA found zero violations or incomplete checks, including zero WCAG 2.2 target-size findings; no route mismatch, page overflow, or console error occurred. Eight overflowing table regions moved right 96 px with ArrowRight and returned to the left edge with ArrowLeft after explicit focus at 390 px. A real Tab walk at 320 px reached all eight table regions. No paper order or safety action was submitted. Manual screen-reader and target-spacing review, populated order/fill histories, saved demo rows, and signed P00-P13 acceptance remain open. Evidence: `docs/evaluations/mission-control-authenticated-accessibility-2026-10-20.json`.

The authenticated production-build focus walk covered all 18 routes at 320, 390, and 1280 px, with 140, 143, and 414 observed Tab stops respectively. Every route returned to its first stop at each width; all measured controls showed a visible pixel-detected focus indicator, with minimum 12 px outline-band change ratios of 4.28%, 9.04%, and 16.10%. Browser errors were zero. A separate pass tested real clipboard paste and simulated composition events in seven reachable text fields across Brokers, Risk Center, and Traces; all passed. No form or action was submitted. Native operating-system IME and manual screen-reader checks remain open. Exact session trails and text probe are in the locally ignored `apps/web/output/playwright/p12-auth-focus-20261020/` and viewport-specific sibling folders; summary and scope are in `docs/MISSION_CONTROL_ACCESSIBILITY_HANDOFF.md`.

A01 0.5.0 now has an authenticated incident-review cohort that calls only A18 0.2.0. The saved PAPER graph binds the task, exact A18 child and immutable proposal; replay after restart serves the same parent receipt. Unauthorized/extra request fields are rejected, health ERROR defers the child, and the integration compares order inventory and system safety before/after to prove neither changes. The test also verifies plan tampering is rejected and both schemas match the pinned Pydantic contract. The focused supervisor/registry/dispatch/graph/API group passes (167). No full backend suite run was needed for this additive cohort; release acceptance, full P07 missions, and P00-P13 remain open. See `docs/TASK_SUPERVISOR_HANDOFF.md` and ADR 0216.

A01 0.6.0 adds an authenticated SANDBOX `HYPOTHESIS_RESEARCH` parent that dispatches only A08 0.2.0 through its existing checked research context. Fixed task/child identities, current health/quarantine, call budget, expiry, receipt and parent deadline checks remain enforced; restart returns the same parent receipt. Operator approval remains required before A09 construction. Regression proves the child stays in SANDBOX and PAPER order inventory/safety state remain unchanged. The full locked backend suite passed (3,233 passed, 43 skipped); after extracting shared plan-context validation, the complete focused A01/A08/graph/API/registry/architecture group passed (171). SANDBOX is not OS isolation; P07 acceptance and P00-P13 gates remain open. See ADR 0217 and `docs/AGENT_RUNTIME_HANDOFF.md`.

A08 0.3.0 preserves the A08 0.2.0 output contract and adds authenticated SANDBOX context compilation with durable five-second leases over proposed hypothesis signatures. Both 0.2.0 and 0.3.0 context routes atomically claim in one SQLite inventory; the 0.3.0 route also includes active signatures in checked research memory and omits leased drafts, while a 0.2.0 request fails closed after losing a claim race. Existing completed 0.2.0 and 0.3.0 graph outputs remain in the 128-record memory window. The focused A08/context/graph/API/registry/supervisor/architecture group passes (188), Ruff and formatting pass, and the retained 0.2 evaluation and 0.3 strict schema match their pinned sources/runtime. The full backend suite predates this increment. Lease files are local coordination state, not independently tamper-proof. A01 0.6.0 still invokes A08 0.2.0 and now participates in the shared lease inventory; independent TRAINING/HOLDOUT identity separation, model routing/calibration, independent acceptance, and P00-P13 gates remain open. See ADR 0218 and `docs/RESEARCH_HYPOTHESIS_HANDOFF.md`.

A01 0.7.0 adds a versioned SANDBOX research supervisor route fixed to A08 0.3.0; A01 0.6.0 stays fixed to A08 0.2.0. Typed v3 schemas, a version-bound catalog, and restart-safe graph receipts are covered. Run focused and full backend verification, then the selected Bugbot review. Broader P07 and P00-P13 acceptance remain open. See ADR 0219.

A01 0.7.0 v3 route and dispatch are complete for this increment. Focused verification passes (165), including exact A08 0.3.0 dispatch, operator-only typed admission, schema parity and restart persistence. Ruff lint/format and diff checks pass. Bugbot found no actionable findings but its isolated test collection lacked `aiosqlite`; a broader API run found one unrelated existing trade-intent schema parity failure. The full backend suite remains unrun. Continue broader P07 and P00-P13 acceptance work.

### P07 A01 validation review cohort

A01 0.8.0 accepts an operator-selected archived validation artifact through a typed SANDBOX route and dispatches exactly A10 0.2.0 using the reference bound in the parent context. Parent inputs contain no HOLDOUT dataset reference or A10 summary; the A10 child retains the checked archive compilation path. The integration proves exact artifact binding, unchanged PAPER orders/safety, operator-only typed admission, generic PAPER-route refusal, and restart receipt reuse. An independently observed A08 quota-denied incomplete-context path was fixed and covered by regression. The focused A08/A01 supervisor/dispatch/source/registry/validation API set passes (146) under the platform Pydantic 2.13.5/Core 2.46.5 profile, and the full suite passes (3,247 passed, 43 skipped). A separate diagnostic run under the untracked workspace Pydantic 2.14.1 lock reported 32 schema parity mismatches; this is dependency-profile drift, not the supported platform verification. The current cohort remains partial/advisory; SANDBOX is not OS-level HOLDOUT isolation. Independent HOLDOUT acceptance, P07 completion, and P00-P13 release gates remain open. Roll back by reverting the route, registry, dispatch and schemas together; preserve existing local graph/context archives. See ADR 0220.

### P07 A01 reviewed construction cohort

A01 0.9.0 accepts an operator-supplied active review reference through a typed SANDBOX route and dispatches exactly A09 0.2.0. Parent context binds only the review reference; child compilation rechecks it through the existing A09 permission and active-review path. Integration verifies inactive and malformed requests are refused, candidate provenance is retained, execution and promotion remain false, PAPER orders and safety state do not change, and the parent receipt survives restart. See ADR 0221. P07 remains incomplete pending independent training/holdout isolation, model routing and quality acceptance; the full P00-P13 release gates remain open.

### P07 Windows HOLDOUT process boundary decision

ADR 0222 selects a distinct Windows LocalService process and per-service SID as the HOLDOUT data owner. The interactive PAPER process will have no direct filesystem access and may receive only aggregate validation/stress evidence over bounded ACL-protected local RPC. At decision time this was only a target; implementation has since progressed as recorded below. HOLDOUT isolation remains false until process and file permission acceptance pass.

The initial HOLDOUT RPC message boundary is implemented and covered by four focused tests: fixed A10/A11 operation pairing, strict schemas, aggregate-only response fields, bounded length-prefixed frames, and request/task/context/status correlation. No service, ACL, store ownership move, or API routing is present yet; isolation remains unverified.

The next slice adds `holdout_service_security.py`: a Windows directory ACL provisioner derives the unique service SID, grants inheritable full control only to that SID and SYSTEM, makes the service SID owner, protects the DACL, and verifies the resulting owner and exact ACE set. It also defines the named-pipe DACL for the service, derives the interactive user SID from the current token, and configures/verifies the per-service SID through the Service Control Manager. Twelve RPC/ACL policy tests pass; an actual token probe returned the current Windows account SID; changed files pass Ruff, format, and diff checks. The provisioner has not been run against current data or by a service-token integration test; service registration/runtime, store migration, routing, and process-access acceptance remain open.

Added the Windows named-pipe client. It checks the connected server process token for the configured service SID before sending, uses bounded overlapped I/O under one total deadline, validates the response length, and does not retry uncertain requests. A Windows integration test starts a spoofed same-user pipe and confirms the client refuses it before writing. The focused RPC/ACL set passes (17), Ruff/format/diff pass. No installed validator service exists yet, so successful service-token requests remain untested.

Added `process_rpc_frame` as the server-side framing and failure boundary: malformed data is dropped; success is schema- and identity-checked; permission, timeout, and internal exceptions are converted to fixed codes with no exception text or raw dataset content. Added a single-client-at-a-time overlapped named-pipe server, Windows service host, and install/upgrade/remove command. Installation configures LocalService, enables and verifies the per-service SID, stores nonsecret service settings in HKLM, protects an absolute data directory, verifies ACLs across existing database/WAL/SHM/backup files, then starts the service. Removal stops and removes the service without deleting the data directory. The service mounts the RPC frame handler but currently returns `SERVICE_UNAVAILABLE`; store ownership migration and A10/A11 operations are not wired. Focused service/RPC/ACL tests passed (25) at that stage. The service has not been installed or run under LocalService, so no isolation claim is established.

### P07 Windows HOLDOUT bounded response delivery

The named-pipe client acknowledges a fully received bounded response before disconnect. This prevents Windows from discarding queued response bytes during pipe teardown. The server keeps the same request deadline through acknowledgment and only accepts the fixed receipt marker; it emits optional fixed-category failure signals without raw exception details. A live Windows server exchange test passes ten consecutive runs. The focused HOLDOUT service/RPC/ACL group passed (26) for that increment. The installed service token, data-store migration, A10/A11 routing, and cross-identity ACL acceptance remain open; isolation stays false.

### P07 HOLDOUT aggregate response contract

The strict RPC schema now represents A11's per-scenario aggregate totals using exact decimal PnL and bounded names, fingerprints, currencies, and order counts. It rejects mismatched scenario counts and checks operation-specific response shapes. The focused HOLDOUT service/RPC/ACL suite passes (27); Ruff, formatting, and diff checks pass. The service domain handler, isolated store migration, API RPC routing, and process/file ACL acceptance remain open.

The package architecture inventory and criticality map now include the three HOLDOUT transport/security modules. The architecture boundary suite passes (10). At the time of this entry, the clean-code descriptor checker was unavailable because `architecture.md` lacked its required block; resolved on 2026-10-23 by declaring the same seven-module domain boundary used in `architecture.json`. The checker now matches 7 domain and 352 application files and reports 1,547 cross-layer imports with zero violations. This remains a scoped domain check, not complete package-wide layering. The complete pytest run was already in progress during the inventory change; later full-suite evidence is recorded in `docs/BUILD_STATUS.md`.

### P07 service-owned HOLDOUT read-only store

Added `HoldoutEvidenceStore` as a read-only view of the isolated HOLDOUT SQLite partition. Opening it requires an existing absolute path; every read rechecks database integrity, capacity, audit-chain integrity, HOLDOUT classification, receipt/raw-byte identity, and event bindings. The Windows service now opens the file from its protected data directory and closes it on shutdown. A verified SQLite staging helper snapshots an existing HOLDOUT database, checks receipts and raw bytes after backup, creates the destination without overwrite, and refuses a hard-link alias; it leaves the source untouched and does not set ACLs. The PAPER API research catalog now opens TRAINING storage only and rejects HOLDOUT publication. The broad API/catalog/HOLDOUT regression set passes 75 tests. Existing HOLDOUT files and validation-package copies remain outside the service boundary; operator-data migration, service handlers, A10/A11 RPC routing, and process/file ACL acceptance remain open, so isolation stays false.

### P07 legacy HOLDOUT catalog recovery

TRAINING-only startup now rejects old mixed catalogs by receipt metadata before selecting raw bytes, with instructions to run an offline migration while the PAPER API is stopped. The `migrate-legacy-catalog` command performs the checked split and reports `acl_secured: false`; it does not claim service ownership or filesystem isolation. Regression coverage verifies metadata-first refusal with corrupt legacy HOLDOUT bytes and successful startup after migration. Focused API/catalog/HOLDOUT/service tests pass (84). Bugbot re-review confirmed the compatibility issue is resolved with no new findings. Real operator-data migration, service domain handlers, A10/A11 RPC routing, and Windows ACL acceptance remain open.

### P07 RPC failure classification

The RPC client now raises `HoldoutRpcError` with only one of the protocol's fixed service failure codes. This lets callers distinguish refusal, timeout, and unavailability while keeping exception details sanitized. Focused RPC/service/read-only-store tests pass (24); A10/A11 routing and service domain handlers are still open.
The subsequent exact-evidence response binding is protocol v2 on `AIQuantHoldout.v2`; service and client need a coordinated upgrade, with no fallback to the v1 endpoint.

### P07 A10 RPC adapter

Historical implementation note: this entry captured an intermediate state. It was superseded by the P07 service-owned package, result, and HOLDOUT publication/refresh paths documented in `docs/P07_HANDOFF.md` and the 2026-10-23 entries below. The installer orchestration regression now confirms archive staging and ACL-gated startup ordering with mocked Windows calls; real service installation and identity/ACL/process-access acceptance remain open.

### P12 saved demo portfolio browser keyboard check (2026-10-23)

The saved positions and pending-orders tables now reuse the shared named, focusable `TableScrollRegion`. A regression test failed before the change and passes after it. In the production build with the isolated synthetic PAPER fixture, 320px and 390px viewports had no page-width overflow and the three portfolio tables scrolled within their own regions; 1280px had no table overflow. A real keyboard Tab walk reached both demo regions, ArrowRight moved the focused positions table, and ArrowLeft returned it to the left edge. Full web suite: 202 passed; ESLint and production build passed (the build displayed Next.js's known-vulnerability upgrade reminder). No orders or safety actions were submitted. This evidence covers this portfolio panel; remaining P12 browser acceptance and P00-P13 gates stay open.

### Web Next.js security upgrade (2026-10-23)

Upgrade `next` and `eslint-config-next` from 16.4.0 to 16.4.2, preserving React 19.2.8 and enabling the built-in `security` reminder policy in `next.config.ts`. The active app installs from `package-lock.json`, reports Next 16.4.2, passes all 202 web tests, ESLint and production build, and has no `npm audit --omit=dev` findings. Synthetic production browser checks at 320/390/1280px verify no page overflow, keyboard reachability, and arrow scrolling of demo tables. Raw npm audit retains five high dependency rows for one high `braces <=3.0.3` advisory through dev-only ESLint tooling; upstream lists no patched release. The offered force repair downgrades the Next ESLint config to 14.x and was not used. See the GHSA advisories [Next.js](https://github.com/vercel/next.js/security/advisories/GHSA-673q-mjm6-r5xr) and [braces](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).

### P07 installer orchestration regression (2026-10-23)

Add a Windows-only installer-path test using four synthetic SQLite archives. It verifies the archives are staged before ACL application, service startup occurs only after ACL success, and an ACL failure leaves the service stopped while preserving source archives byte-for-byte. The complete `test_holdout_validator_service.py` file passes (15 tests); the combined P07 RPC/service/catalog/CLI/API regression group passes (85 tests). A fresh full backend run including this regression passes **3,314 tests with 47 skipped in 1,745.35 seconds** on Pydantic 2.13.5/Core 2.46.5. Scoped Ruff lint/format and `git diff --check` pass. Windows service and ACL operations are mocked, so this does not prove LocalService token behavior, real file/pipe ACL enforcement, or process isolation. P07 and P13 remain open.

### Enforce the declared domain boundary with the clean-code checker (2026-10-23)

Add a `clean-architecture` block to `.clean/architecture.md` matching the seven deterministic domain modules already listed in `.clean/architecture.json`. The check parses the declaration, matches 7 domain and 352 application files, inspects 1,547 cross-layer imports, and reports zero violations. Its scope is the deterministic domain boundary; selected control, agent/strategy, persistence, and integration rules remain covered by the architecture inventory tests. Current full-suite and release-gate status is summarized at the top of `docs/BUILD_STATUS.md`; P07 service identity/ACL acceptance and P13 sign-offs remain open.

### P12 realtime frame metadata contract (2026-10-23)

The browser projection now retains the schema version, instance sequence, source timestamp (`world.as_of`), client receive timestamp, and server validity lease. The new test failed before the implementation because `schema_version` was dropped by the projection, then passes with the rest of the realtime parser suite (10 tests). The complete web suite passes (203), ESLint and production Next.js build pass. This closes the client projection fields for the current full-snapshot contract; deterministic six-state visual baselines/CI, all-view/load acceptance, and independent review remain open.
