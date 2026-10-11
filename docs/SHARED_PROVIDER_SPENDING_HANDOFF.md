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

## Observations and console limitation

One verified observation can expose aggregate role counters and selected model
role counters/accounting/fingerprint under the same audit head. Sanitized adapter
status publishes the configured owner's two counter rows. Credentials, rate
references and local paths are not published. Unavailable custody withholds
counters and denies generation.

The protected review-spending endpoint and Costs panel retain their standalone
OpenAI schema. That old contract withholds a shared book as unavailable because
it cannot label mixed accounting correctly. No shared counters are relabeled as
one OpenAI model, and duplicate adapter observations are not summed. The typed
shared-book observation and Costs presentation remain development work. No
frontend file changed in this increment.

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
platform allocation, additional families, shared Costs, recovery spending,
migration/retention, large-journal throughput/latency, deployment custody, browser
and independent acceptance. Unknown Docker custody remains untouched. All P00–P13
remain incomplete. See ADR 0138 and evaluation 0138.
