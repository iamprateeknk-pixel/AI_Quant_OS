# Reviewed hypothesis to inert strategy candidate

Date: 2026-10-04

## Delivered

A09 0.2.0 is PARTIAL/SHADOW. An operator can authorize construction from one
exact hypothesis in a checked completed A08 0.2.0 graph. The review retains the
original graph digest, full hypothesis, rationale, local operator identity basis,
review time and expiry. It authorizes inert construction only. Alpha acceptance
remains unassessed; execution and promotion flags remain false.

Reviews expire within 24 hours (default one hour). Repeating the same review ID
returns its original receipt without extending expiry; changed identity/rationale
conflicts. Withdrawal is immutable and blocks subsequent construction, including
after context capture. Reviews and withdrawals persist with SHA-bound audit events
in the same transaction. Current review status also checks A09 build permission.
The existing local operator token does not establish named operator sessions.

Three fixed server bindings feed A09: hypothesis_spec through
RESEARCH_MEMORY_READ/TRAINING, and strategy_sdk/feature_catalog as fixed GENERAL
environment metadata through WORLD_STATE_READ. There is no client-selected path
or feature source. The SDK profile declares a completed-bar direction interface,
two observed package source hashes, and an unavailable proposed-source runner.
The feature catalog allows only PREVIOUS_CLOSED_BAR_DIRECTION with two prior
closes. Source capture and context validity are bounded to five seconds and to the
review's remaining validity. Context/checkpoint, task, scope, current capability
and review gates are checked before construction and after publication.

The task-bound CANDIDATE_CREATE tool publishes a deterministic inert bundle:

- Candidate source for the reviewed continuation or reversal rule, one fixed
  instrument, long/short/flat unit targets and chronological prior closes.
- A typed StrategyGenome and exact genome hash.
- Proposed test source, including insufficient history, rising/falling/flat prices,
  wrong instrument and nonchronological input cases.
- Original review, context/task binding, source hashes, SDK hashes and assumptions.

Files are retained as text in checked SQLite/JSON artifacts. The parent never
imports, compiles or executes them. Source interpolation first revalidates all
typed review fields, including model copies, so arbitrary text cannot replace
the fixed instrument. The built-in strategy registry and all four sealed source
closures remain unchanged; its runner refuses these unregistered proposed sources.

## Operator workflow

Use persistent local paper state and the existing operator token:

1. Inspect a completed A08 graph and its hypothesis signature.
2. `POST /api/v1/sandbox/research/reviews` with review_id, run_id,
   hypothesis_signature, rationale and optional valid_for_seconds.
3. `POST /api/v1/sandbox/strategy-builder/context` with task_id and review_ref.
4. `POST /api/v1/sandbox/strategy-builder/execute` with run_id and context_ref.
5. Inspect the graph and `GET /api/v1/sandbox/strategy-builder/candidates/{candidate_ref}`.
6. Review status is available at `GET /api/v1/sandbox/research/reviews/{review_ref}`;
   withdraw with `POST .../{review_ref}/withdraw` and a reason.

Resume uses `POST /api/v1/sandbox/strategy-builder/graph-runs/{run_id}/resume`.
Paper execution/resume routes refuse these sandbox contexts. Completed graph
receipts remain archival; their context_current field reports runtime permission
and context expiry, not ongoing review authorization. Check the review and builder
admission for a new operation. An expired or withdrawn review cannot authorize a
new candidate; its original artifact remains inspectable. The preview without an
operator token refuses review/build operations.

## Evidence

```powershell
python -m ai_quant_os.strategy_builder_evaluation tests/fixtures/strategy_builder_baseline.json
```

Two declared synthetic scenarios replay actual A08 data intake and graph output,
operator review, A09 context/graph, candidate publication and fresh app restart.
Both preserve exact review/candidate/graph records and unchanged controls. The
report retains eight named new source pins, evaluator/fixture hashes and typed
outputs/bundles. Review, candidate bundle and agent envelope schemas are retained.
These pins do not cover a complete transitive executable closure. Trusted replay
subprocesses release cached Windows SQLite handles; they do not isolate or execute
proposed code. A deliberately wrong expected rule fails replay matching.

Tests also cover current role denial, strict task/tool/scope binding, malicious
model-copy interpolation, output recipe forgery, withdrawal races, original expiry
on retry, context expiry after publication, actual owned-write cancellation/join,
capacity, zero-file/missing metadata refusal and deleted-artifact journal damage.
Expiry/cancellation after publication can retain an inert archival artifact while
withholding success. Enabled monitoring observes graph receipts before reply.
Full regression and preservation evidence is recorded in BUILD_STATUS.

## Remaining work and rollback

This is a deterministic two-rule construction profile. Broader feature catalogs,
model-driven generation, complete dependency/source capture, isolated compiler/test
execution, proposed-source runner integration, independent validation/holdout
quality, calibration, promotion and live authority remain open. SANDBOX is a
permission scope; OS confinement is unverified. SDK hashes observe local bytes
and are not independent attestation or archived dependency objects. Shared research
storage has no physical/statistical holdout isolation. Local audit has no external
anchor. A01 0.6.0 can dispatch A08 0.2.0 in the fixed SANDBOX
`HYPOTHESIS_RESEARCH` cohort. A09 construction remains a separate
operator-reviewed action; this cohort does not dispatch A09 or A18 through the
builder workflow.

Preserve the complete state root before reverting, especially strategy-builds,
classified research data, world/context/graph and health/quarantine journals. Keep
withdrawals and original review expiry. Older runtimes cannot execute A09 0.2.0
and must never register or run retained proposed source to work around that limit.
