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
