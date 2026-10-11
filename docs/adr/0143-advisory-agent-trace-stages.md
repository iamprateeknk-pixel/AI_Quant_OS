# 0143: Trace actual advisory classification and admitted provider invocation

Date: 2026-10-11. Development, PAPER only.

Add native SDK child spans for world-state capture, fast classification, deep
review and verified archival in the existing protected classification endpoint.
Admitted provider wrappers accept a standard context-manager factory, disabled
by default. The composition root binds fixed local span factories after runtime
initialization and invokes them only at request time. No SDK dependency enters
domain policy, classifier/reviewer contracts or the durable envelope schemas.

Provider invocation spans start after original logical admission/reservation.
They include the adapter's guarded method and post-call admission check, so a
span does not certify a remote HTTP request or billable call. A denied logical
admission creates no invocation span and performs no provider call. Parentage is
inherited across await/wait_for using the original server-created HTTP context.

Allowlist original UUID envelope IDs, review-parent envelope UUID, next-route
and review-status enums. They are advisory evidence IDs, distinct from SDK trace
IDs. Omit task/world/evidence/prompt/rationale/provider identity and exception text.
Fixed exception status also covers cancellation from the existing timeout; native
fallback, quotas, archival, zero-order authority and HALT remain unchanged.

Rollback by reverting 0143 observation wrappers/allowlist; all original schemas,
journals, SDK dependencies and deterministic policy remain unchanged. No migration
or reset. Full causal agent-to-risk/order/fill linkage, durable SDK trace binding,
other eighteen-role missions, collector deployment, independent acceptance and
all P00–P13 completion remain pending.
