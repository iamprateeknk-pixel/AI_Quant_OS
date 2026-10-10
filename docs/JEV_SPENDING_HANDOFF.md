# Jev billable-input admission

Development increment 0137, paper scope. No credentialed provider request was made.

## Contract and accounting

The current [TypeSafe HTTP contract](https://docs.typesafe.ai/api) has a single
evaluation endpoint and returns model identity plus input/output token usage.
The [model contract](https://docs.typesafe.ai/models), observed 2026-10-10, publishes
`jev-1.13.0`, a 64k whole-request context limit and 32k state-plus-longest-question
limit. It charges USD 0.042 per million input tokens; outputs are free. The fixed
four-option Choice request remains unchanged.

This adapter reserves **65,536 input tokens** before each request, rounding the
published whole-request context limit upward. This is a conservative contract-based
bound, not a local tokenizer estimate, byte-to-token formula, token-count endpoint,
or provider-enforced output cap. There is one question per request. The smaller
per-question context limit can reject a request; it cannot enlarge its reservation.

`JevInputSpendPolicy` admits only that reviewed model and pins the input ceiling
and `JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING` accounting in its canonical policy.
The operator must declare an integer input price ceiling of at least **42 USD
nanodollars per token** and an explicit rate reference. Output price must be zero.
No operational quota, credential, funding or paid-provider activation was created.
These limits count **billable input tokens**, not input-plus-output totals. Raw
reported outputs are retained for settlement identity but consume no token/money
quota. No invoice or actual service-plan price is certified.

## Configuration

Disabled remains the default. Environment activation now requires:

- `AI_QUANT_JEV_ENABLED=true`, server-only `AI_QUANT_JEV_API_KEY`, and
  `AI_QUANT_JEV_MODEL=jev-1.13.0`;
- `AI_QUANT_CLASSIFY_OWNER=agent@version`, with the existing registered paper
  `MODEL_REVIEW` permission;
- `AI_QUANT_JEV_SPEND_PATH`, the original durable ledger path;
- `AI_QUANT_JEV_SPEND_POLICY`, a bounded policy JSON file for first creation.
  This may be omitted only when reopening the original pinned ledger.

Policy JSON has schema version, accounting, input reservation ceiling, model,
input/output nanodollar rate ceilings, rate reference and all eighteen `SpendRule`
rows. The additive `jev-input-spend-policy.schema.json` documents its shape.
Runtime also checks daily/monthly and reserve inequalities. Per-role quotas span
registered versions. The existing logical tool-call ledger remains separate.
The OpenAI reviewer policy, accounting, schemas and Costs view remain unchanged.
Jev and OpenAI do **not** yet share one aggregate monetary ceiling or Costs table.

Trusted direct library fixtures may still construct an unconfigured classifier;
its status explicitly says `NOT_CONFIGURED` and does not assert spending admission.
Production embedding must supply the ledger and the admitted route wrapper.

## Reservation and settlement

Reuse of the pinned audited SQLite store preserves exclusive first creation,
immutable policy, canonical original creation pin, full inventory/audit checking,
transactional concurrent admission and bounded archive capacity. Neither model
policy silently converts the other's ledger. Existing OpenAI records retain their
original wire bytes and accounting.

Before transport, the classifier checks the ledger, reserves the complete input
ceiling and declared monetary ceiling, and rechecks source freshness and current
route permission. The wrapper requires the route owner to match the spending owner.
Blocking database work runs outside the event loop. A changed permission or expired
source after reservation stops dispatch and retains the committed hold.

Only bounded, unambiguous, matching-model native usage can settle a hold. It is
charged even when the Choice answer is rejected or permission is revoked during
the response. Rejected model identity, missing/oversized/duplicate usage, transport
failure and cancellation preserve the full hold, including across restart and UTC
day/month rollover. Identical settlement is idempotent; conflicting usage fails.
An input report above the reserved contract ceiling is retained and latches
`BREACHED`; subsequent requests are denied. Free output counts do not trigger an
input-budget breach. No retry, refund, unknown-outcome reset or automatic cleanup
was added. Protected incident reserves cannot be spent by routine requests.

The existing sanitized provider status distinguishes input admission from total
token admission, shows the selected owner's original counters and fingerprints,
and marks missing/corrupt custody unavailable. Provider failure continues to use
the quantitative paper fallback. Emergency paper controls remain independent.

## Verification and remaining acceptance

Tests use real SQLite journals, concurrent/reopened stores, native API composition
and `httpx.MockTransport` for the exact official request. They exercise limits,
protected reserves, unknown rollover, breach retention, free outputs, invalid
answer usage, cancellation, source/permission rechecks, owner mismatch, corrupt
custody, quantitative fallback and emergency controls. Original failure evidence
and the fresh staged verification are retained in evaluation 0137.

Actual credentialed request/usage correspondence, whether published context limits
bound every billable internal overhead token, current account pricing/surcharges,
provider failures/billing, invoice reconciliation, efficient reservation tightening,
large-journal throughput, alternate models, shared all-provider budgets, recovery
reserve spending, retention/migration and independent acceptance remain pending.
The observed published contract supports this development bound; these external
assumptions are not an independently certified spending guarantee. Browser
acceptance remains pending under the earlier refusal. All P00–P13 remain open.
