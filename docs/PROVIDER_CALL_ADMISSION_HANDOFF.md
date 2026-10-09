# Legacy provider call admission

Increment 0102, 2026-10-09. Partial P06/P07/P12 delivery; all P00-P13 remain open.

## Behavior

Both configured classification and deep-review providers in `create_app` now pass
through durable call admission before provider dispatch. The same 18-role quota
journal covers graph tools, context fetches and these logical provider attempts.
Versions and both routes share the declared role's daily/monthly routine capacity;
protected recovery capacity remains unavailable to routine calls.

Declare server accounting ownership using `AI_QUANT_CLASSIFICATION_OWNER` and
`AI_QUANT_REVIEW_OWNER`, each `agent@version`, or typed `ProviderOwner` application
configuration. Only cataloged versions of roles with MODEL_REVIEW capability can
own these routes (currently A02/A03). An explicit declaration is accounting
ownership, not graph mission execution, calibration, approval or promotion.
No owner is inferred from a client task, model name or provider credential.
The example environment leaves ownership and provider activation unset.

When a configured or retained budget exists, an undeclared owner prevents
provider dispatch. Corrupt or unavailable archives also refuse calls. With no
configured or retained budget, unowned legacy provider behavior is preserved;
status explicitly reports BUDGET_NOT_CONFIGURED. Invalid static ownership fails
configuration. Quarantine denies before charging. Permission and source expiry
are rechecked after asynchronous reservation and after provider results.

Reservations commit before dispatch; failures and interrupted admitted attempts
are not refunded. Cancellation during reservation withholds provider dispatch,
although the admission thread may already have committed a charge. Logical
admitted attempts include adapter circuit or local request-limit refusal; they
are not physical HTTP request counts, reported token counts or billable costs.

The reservation request hash binds provider identity, full world-state fingerprint,
instrument, purpose and (for reviews) the typed parent decision. Classification's
legacy provider protocol has no task identifier. Source content, keys and private
provider errors are not copied to the quota reservation journal.

`GET /api/v1/decisions/provider` adds classification_call_admission and
review_call_admission metadata: state, declared owner and explicit false values
for token/monetary enforcement. Existing raw adapter process-local status remains.
Existing quantitative fallback and review failure behavior contain denied calls;
paper risk and emergency safety controls remain usable when quota storage fails.

## Evidence and limits

Trusted fixture tests cover durable restart/version sharing, unowned retained
policies, zero-quota mocked provider transports, shared concurrent classification
and review, quarantine, source expiry, cancellation, corrupt pins, static owner
configuration and unconfigured compatibility. API checks exercise real route
wiring, sanitized public status and emergency controls after corruption.

No paid provider, broker, credential or live-capital call was made. No client
workflow or immutable schema changed. Independent browser acceptance remains
pending from the earlier rejected preview launch. Direct standalone adapter use,
pre-compilation snapshot preparation, token and monetary admission/settlement,
recovery spending and full cost governance remain outside this increment.
See evaluations/provider-call-admission-0102.json for final verification evidence.
