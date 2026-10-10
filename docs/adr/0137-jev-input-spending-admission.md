# ADR 0137: Contract-bound Jev billable-input reservations

Date: 2026-10-10. Status: development implementation; external acceptance pending.

TypeSafe's current Jev 1.13 contract prices input tokens only and publishes a whole
request context limit. Copying OpenAI's input-count preflight or maximum-output
field would invent an API. Sending an unreserved request and counting usage only
afterward cannot admit spending safely across restart or concurrent callers.

Reserve a rounded-up 65,536-token whole-input ceiling under a distinct immutable
Jev input-only policy and an operator-declared rate ceiling, restricted to the
reviewed `jev-1.13.0` contract. Reuse the original audited store's custody and
transactions, delegating token/money/breach calculations to the selected concrete
policy. Preserve OpenAI's original format, semantics and schemas; add a Jev schema.

Require the ledger and declared classification owner for environment activation.
Bind the spending owner to route ownership and recheck permission/source around
database awaits and transport. Settle matching native usage independently of
answer acceptance; retain unknown holds and above-bound reports. Outputs remain
reported data with zero billable weight. Status must say input-only, no total-token
or invoice certification. Routine callers cannot consume protected reserves.

This conservatively reserves much more than ordinary requests use. A tighter
reservation requires an actual supported count contract or verified tokenizer,
not bytes or guessed overhead. Credentialed correspondence, billable internal
overhead coverage, pricing/invoices, aggregate all-provider budgets, recovery
spending, retention/migration/throughput and P13 remain open. See
JEV_SPENDING_HANDOFF.md and evaluation 0137.
