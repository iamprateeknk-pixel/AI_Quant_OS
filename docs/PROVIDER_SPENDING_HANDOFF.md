# Durable reviewer token and declared-rate admission (0135)

## Delivered

OpenAI paper review reserves official counted input tokens plus its enforced
maximum output before generation. The reservation commits in an independently
pinned audited SQLite ledger. Immutable UTC daily/monthly token and nanodollar
ceilings are shared across versions of each of eighteen roles. Routine admission
subtracts protected recovery reserves; this increment cannot spend those reserves.
The earlier durable logical-call ceiling and process-local circuit remain separate.

Preflight includes the exact pinned model, evidence, instructions, structured
output schema and empty tools sent to generation. Generation disables truncation
and keeps max_output_tokens. Count replies require the official object identity
and a bounded strict integer. Count failure, unavailable custody, depleted capacity,
changed permission or expired input prevents generation. The route owner must match
the spending owner. Permission/source checks run after asynchronous admission.
Count and generation are two HTTP requests under one logical review attempt.
Both use bounded replies, no redirects/proxy environment and server-only keys.

## Accounting and uncertainty

The policy supplies positive integer **USD nanodollars per input/output token**
(one nanodollar is 10^-9 USD), not an automatically discovered provider price.
Reservation money is input * input_rate + maximum_output * output_rate, using
integer arithmetic. Matching-model usage settles at the same immutable rates and
releases only unused capacity. Incomplete/rejected advisory content is still
charged. Identical settlement is idempotent; changed settlement is rejected.
Cache discounts are not assumed. This is declared-rate admission accounting,
not a provider invoice or billing-period statement.

Malformed/missing usage, wrong model, timeout, interrupted reservation and
cancellation retain the full hold. Unknown holds count in every later UTC period
until settled; restart and rollover cannot erase them. Settled charges belong to
their original reservation period. Above-bound reports are retained and latch
BREACHED across the ledger. There is no automatic reset, refund, retry, uncertain
hold resolution, rollover cleanup or breach recovery.

Every operation verifies original creation pin, immutable policy, audit chain,
inventory, canonical records, identities, settlement order and UTC clock.
Writes use SQLite BEGIN IMMEDIATE and FULL synchronization; mode=rw prevents
creating a replacement missing DB. Incomplete creation fails closed. Limits:
20,000 events / 16 MB; 1,024-byte records; 512-byte audit events; 64 KiB policy.
Trusted host filesystem custody remains an assumption. External attestation,
disaster recovery and production throughput are not certified.

## Configuration

The environment factory defaults to disabled. Enabling now requires:

- AI_QUANT_REVIEW_ENABLED=true, server-only AI_QUANT_REVIEW_API_KEY and an
  explicit dated AI_QUANT_REVIEW_MODEL;
- AI_QUANT_REVIEW_OWNER=agent@version with registered MODEL_REVIEW capability;
- AI_QUANT_REVIEW_SPEND_PATH for the persistent ledger;
- AI_QUANT_REVIEW_SPEND_POLICY for first creation. This bounded policy file may
  be omitted when reopening original custody. A supplied changed policy is refused.

Use additive provider-spend-policy.schema.json plus runtime reserve/ceiling
inequality validation. Exactly one A01–A18 rule, the pinned model, declared rates
and an explicit rate reference are required. No operational price/budget is invented
or enabled here. Preserve DB and pin together; removing configuration is not a
migration. Factory activation without spending configuration now fails closed.

Trusted embedded callers explicitly inject spend_store and spend_owner. Direct
unconfigured library fixtures retain prior behavior and report NOT_CONFIGURED;
production embedding needs configured admission and independent review. Local
supervision still disables providers. Sanitized provider status exposes only the
review owner's counters, policy/head hashes and accounting basis, never paths,
prompts or credentials. Older logical-call schemas retain their original meaning.

## Evidence and remaining scope

Tests cover restart, concurrent SQLite owners, UTC ceilings, version sharing,
recovery protection, unknown-period carry, money exhaustion, idempotent settlement,
changed/missing custody, clock/model rejection, over-bound reports, schema/decoder
coherence, exact preflight, cancellation, malformed counts, permission changes,
expiry, factory setup and actual API quantitative fallback/emergency controls.
Development used actual adapter/API HTTP fixtures, with no real provider request.
Evaluation 0135 records fresh staged identity and focused regressions.

Actual credentialed count/generation correspondence and price-ceiling sufficiency
need acceptance. Non-generation endpoint billing, service tiers, cached/write
tokens and invoices require independent validation. Shared multi-provider/model
ledgers including Jev, recovery spending, Cost Center integration, retention,
migration, throughput and independent critical review remain open. There is no
claim that every agent/model is fully governed. All P00–P13 remain incomplete.

## Rollback

Disable review before reverting. Retain ledger, pin and evidence; never delete
custody to regain capacity. Older order/world/risk/call-budget schemas and DBs
require no migration. Reverting environment admission restores older unbudgeted
setup, which is not approved production governance.

## References checked 2026-10-10

- [Input count API](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count)
- [Token counting](https://developers.openai.com/api/docs/guides/token-counting)
- [Response output cap](https://developers.openai.com/api/reference/resources/responses/methods/create)
