# Research hypothesis mission

Date: 2026-10-04

## Delivered

A08 0.2.0 is PARTIAL/SHADOW. Its protected SANDBOX graph drafts prospective
experiments from checked training CSV, prior completed A08 graphs and checked
paper world archives. The global capital mode remains PAPER. SANDBOX is a
permission scope; it establishes no operating-system isolation.

Three server bindings supply historical_catalog (HISTORY_READ/TRAINING),
research_memory (RESEARCH_MEMORY_READ/TRAINING) and world_state_archives
(WORLD_STATE_READ/GENERAL). The catalog enforces current TRAINING_READ permission
before and after reading. Capture checks each store separately; it is not an
atomic observation across stores. Publication uses owned thread completion,
then a bounded context and audited checkpoint with current permission/expiry
checks. Graph execution rechecks admission and binds output to the retained recipe.

The screen counts pairs of consecutive nonzero close-return directions.
Eight eligible pairs are required; at least 75% persistence drafts a continuation
test, at most 25% drafts a reversal test. Intermediate patterns and missing/flat
data withhold drafts. These thresholds are a predeclared development screen,
not significance, calibrated confidence or accepted alpha. Confidence stays zero,
uncertainty one, and escalation mandatory.

Each draft specifies two preceding closes, entry at the following bar open,
separate chronological out-of-sample data, measured costs, causal replay,
multiple-testing policy and an explicit rejection condition. Historical spread
ranges refer only to paper quotes marked fresh at their original snapshot.
Old snapshots remain ARCHIVAL_ONLY. Commission, slippage and financing are
unavailable; no broker cost is fabricated.

All eligible datasets are counted. At most four drafts are returned, ranked by
observed directional deviation and stable signature; remaining drafts are counted
and deferred. Signature uses instrument, dataset fingerprint and rule. Prior
completed graph outputs suppress matching drafts across restart. Novelty applies
only to completed graphs captured for that context: concurrent contexts can
produce the same draft. There is no global novelty reservation.

## Operator workflow

With the existing operator token and persistent paper database:

1. Register declared historical CSV through `ai-quant-research-data` or
   `POST /api/v1/paper/research/datasets` with an immutable TRAINING/HOLDOUT label.
2. `POST /api/v1/sandbox/research/context` with `{"task_id":"research-review"}`.
3. `POST /api/v1/sandbox/research/execute` with that context_ref and a unique run_id.
4. Inspect the durable receipt at `GET /api/v1/agents/graph-runs/{run_id}`.
   Resume only through `POST /api/v1/sandbox/research/graph-runs/{run_id}/resume`.

Paper execution and paper resume reject sandbox contexts. The preview without an
operator token refuses these protected operations. Enabled health monitoring
observes each research receipt before reply; a controlled invalid result was
verified to quarantine the exact A08 version without relying on periodic polling.

## Evidence

Run the retained development replay:

```powershell
python -m ai_quant_os.research_hypothesis_evaluation tests/fixtures/hypothesis_baseline.json
```

Five explicit synthetic cases test continuation, reversal with a recorded spread,
flat prices, empty catalog and exclusion of HOLDOUT data. Each uses the real
protected API, source catalog, persisted graph and fresh app restart. The report
retains six named source hashes, evaluator and fixture hashes, source bindings and
typed outputs; `hypothesis-spec.schema.json` matches the current output model.
Trusted subprocesses release cached SQLite handles on Windows. They do not
execute a proposed strategy or provide a candidate sandbox.

Focused checks additionally cover bounded batch deferral, strict resource labels,
altered receipt/dataset rejection, source/output claim forgery, future/oversize
collections, graph-row deletion, world-byte corruption, operator admission,
five-second expiry, cross-scope resume rejection and immediate health observation.
The unchanged research receipt wire contract was extracted from its SQLite module;
existing installed CLI receipts and schema remain compatible. Catalog source
bytes changed intentionally as part of this dependency correction.

Full verification and preservation results are recorded in BUILD_STATUS.

## Remaining work

Shared storage provides no physical holdout separation or independent audit
anchor. Context summaries rely on trusted local publishers; compact hashes are
not independent evidence of authentic market data. Per-read durable auditing,
real provider feeds, global novelty scheduling, model routing, calibration,
automatic strategy construction, isolated execution, independent holdout quality,
promotion and live broker authority remain open. This mission never runs its
drafted trading rule. A01's sealed six-specialist policy defers A08 and A18 as
unsupported; its earlier source and evaluation pins remain unchanged.

## Rollback

Preserve the whole state root, including classified raw data, context checkpoints,
graph journal, world archive and health/quarantine state. A rollback may retain
historical A08 receipts but the older runtime cannot execute the new mission.
Never relabel holdout data or discard quarantine to make an older reader work.
