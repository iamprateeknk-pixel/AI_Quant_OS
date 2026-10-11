# ADR 0138: Atomic aggregate role spending across model policies

Date: 2026-10-11. Status: development implementation; external acceptance open.

Separate provider ledgers cannot enforce one role's aggregate spending. Model
accounting differs, so summing counters under one model's label is inaccurate.

Pin a bounded inventory of concrete model policies and eighteen aggregate role
rules in an additive shared policy. Reuse the original audited store and unchanged
reservation/usage records. Resolve each reservation's own model for bounds, token
weighting, rates and breach checking. Check aggregate and selected model capacity
within the same transaction. Preserve unknown holds and deny the whole book after
any unresolved bound breach.

Bind both actual adapters to the admitted model while retaining source/permission/
owner checks. Observe both counter levels under one original head. Preserve
standalone formats and schemas; the old review Costs view withholds shared
accounting until an accurate additive projection exists. Configuration explicitly
selects the same book; no migration, reset or deployment-wide guarantee is implied.

Shared Costs, platform allocation, additional model families, recovery spending,
custody/migration/retention/throughput, credentialed billing and independent P13
acceptance remain open. See SHARED_PROVIDER_SPENDING_HANDOFF.md and evaluation 0138.
