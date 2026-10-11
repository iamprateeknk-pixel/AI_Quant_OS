# Shared provider spending admission

Development increment 0138, 2026-10-11. Paper scope; no paid provider call or
operational budget was activated.

## One original book

`SharedSpendPolicy` pins all eighteen aggregate role rules and one to eight unique
model policies. Each retains its original identity, price ceilings, individual
role rules and accounting. OpenAI counts input plus output tokens; reviewed Jev
counts billable input only and prices outputs at zero. Shared token quotas sum
**each model's counted tokens**, not every physical token or invoice line.
Monetary amounts add exact declared USD nanodollar ceilings.

Global and selected model role ceilings, including protected incident reserves,
are checked under the original SQLite `BEGIN IMMEDIATE` transaction. Different
models/versions cannot give a role another aggregate quota. Separate connections
using the same original book cannot both spend its last capacity. Model limits
still apply when aggregate capacity remains. A role's allocation is independent
of other roles; no separate platform-wide ceiling over all roles was added.

Reservation/event shapes are unchanged. Each reservation's immutable model policy
validates its bound and computes charges. Native settlement retains that model's
semantics, including free Jev outputs. Unknown reports retain full original holds
across UTC periods and restart. A retained bound breach in any model denies every
model and role in that book. No retry, refund, reset or reserve spending was added.

## Configuration and custody

Both real adapters accept their original standalone policy or a shared policy
admitting their pinned model. Constructors verify selected model/accounting under
original custody. Source, permission, owner and asynchronous-boundary checks remain.

To use one shared book through environment configuration:

- Set `AI_QUANT_JEV_SPEND_PATH` and `AI_QUANT_REVIEW_SPEND_PATH` to the same original
  shared ledger path. Both adapters verify its creation pin and audit.
- Supply the shared policy JSON through the corresponding `*_SPEND_POLICY` variables
  for first creation. These may be omitted when reopening the original book.
- Retain the existing server credentials, disabled-by-default opt-in, reviewed
  model IDs and declared `AI_QUANT_CLASSIFY_OWNER` / `AI_QUANT_REVIEW_OWNER`.
- Choose explicit global/per-model quotas and rate ceilings. No operational default
  allocations or credentials were installed.

The additive shared policy schema references both original local model schemas.
Offline verification resolves pinned local resources without network access.
Runtime also enforces unique model IDs, the 64 KiB policy capacity, concrete
policy types and quota/reserve inequalities. Nested shared policies and aliases
are rejected; model/rule sorting preserves canonical identity across input order.

Standalone books retain their exact policy/event formats. The loader never
converts or merges old journals. Old ledgers/unknown holds must remain retained;
changing configuration paths is not an approved migration or reconciliation.
Admission applies to the one explicitly selected book. Separate configured books
remain separate budgets; independent deployment/configuration custody is pending.
Replacing an original policy, rate or inventory is rejected.

## Protected observations and Costs

Increment 0139 adds `GET /api/v1/providers/shared-spending`. Authorization is
checked before and after observation. One original read transaction verifies the
journal and captures all eighteen aggregate rows and one to eight complete model
groups under the same head. Configured sources and owners are bound around that
read; separate paths, incomplete configuration, changed bindings or unavailable
custody withhold combined counters. Reading leaves the original head unchanged.
Credentials, local paths, rate references and request hashes are not published.

The typed observation enforces native model accounting and exact aggregate sums.
Integer money is serialized as canonical strings. Costs displays one aggregate
pair of tables and expandable model tables, owners, UTC periods and original
identity/fingerprints. Jev input-only and OpenAI input/output counting are labeled.
Unknown full reservations and breach evidence remain visible. BigInt formatting
retains exact nanodollar amounts, including values above JavaScript number precision.

The original review-spending endpoint/schema remains unchanged. A verified shared
panel suppresses its standalone presentation to avoid duplicate book capacity.
Other shared states retain the standalone panel where available. Logical call
accounting is separate and never summed into this book. The server uses a fixed
private endpoint, session cookie, no-store, redirect rejection, timeout and bounded
256 KiB response; other protected reads retain their existing bounds.

The view is a point-in-time snapshot with an explicit native GET refresh. It does
not renew a live lease or perform provider calls, reserve spending, retries,
refunds, migration or reset. Browser acceptance is pending under the preserved
prior launch refusal. See ADR 0139 and evaluation 0139.

## Verification and remaining work

Real reopened/concurrent SQLite tests cover cross-model versions, global token/
money ceilings, protected reserves, model ceilings, native rate differences,
unknown rollover, global breach denial, unsupported inventories, immutable custody
and environment reopening. Actual adapters use fixed official HTTP mocks: an
OpenAI generation hold blocks Jev before transport, then native settlement releases
unused capacity for an independently reserved Jev request. Full API fixtures check
quantitative fallback, no orders, emergency controls and sanitized status under
shared-budget failure. Existing provider/API/role/context/control regressions run
against fresh staged source with old schemas preserved.

Actual billing and Jev billable overhead-bound correspondence remain open, as do
platform allocation, additional families, realtime Costs, recovery spending,
migration/retention, large-journal throughput/latency, deployment custody, browser
and independent acceptance. Unknown Docker custody remains untouched. All P00–P13
remain incomplete. See ADR 0138 and evaluation 0138.
