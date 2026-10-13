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

## Rollback

Revert the specific increment commit to remove its API and admission path. The candidate assignment database change is additive; rollback leaves its records inert, and those records must not be deleted as part of code rollback. The daily comparison and reality-gap alert increments read retained evidence and do not rewrite those archives. The multi-batch comparison change has no persisted state; rollback only restores the prior route/domain selection behavior.
