# Broker capability registry in Mission Control — increment 0117

## Connected read path

The Brokers page fetches the existing paper broker snapshot, protected demo
heartbeat and protected capability registry in parallel. A fixed server mediator
reads `/api/v1/brokers/capabilities` through the current operator session. The
session stays in its server-only HttpOnly cookie, and the existing local-origin,
no-store, timeout, redirect-refusal and body-bound controls apply.

The capability table distinguishes the paper simulator, Exness demo observation
library and unsupported Exness/IBKR/Zerodha execution skeletons. Eligibility scope
and paper broker admission remain distinct from native release. Details list
friendly interface names, original profile version, execution-router binding,
actual mapped paper instruments and unverified native acceptance. No enablement,
credential entry, enrollment, connection or order controls are introduced.

The table uses existing operational panel/table/detail styles, semantic column
and row headers, a caption and explicit sign-in/unavailable states. Missing
profiles are withheld rather than filled with guessed broker capabilities.
Denied paper eligibility and absent paper mappings remain explicit.

## Public projection

The frontend admits the complete five-profile 0.1.0 registry and checks exact
implementation/interface vocabulary, expected module binding, duplicate/unknown
identities, supported mapped symbols, bounded collections and explicit false
live release/portfolio authority. Paper admission cannot be true without paper
identity, router binding, eligibility and local-simulation scope. Private extra
fields and source module names are removed from rendered props. Future profile
versions require explicit reader support.

The test fixture is generated from the actual current backend registry. A backend
integration check compares every fixture value to the default protected API
response; it is not an invented readiness sample. Frontend parser/render tests
consume this fixture and exercise malformed/unauthorized alternatives.

This is a fetched snapshot with explicit refresh. Declared interfaces and local
simulation eligibility do not attest native connectivity, vendor entitlement,
legal compliance, risk admission or external execution approval. The existing
paper snapshot and demo heartbeat keep their original meaning.

## Verification and limits

Frontend checks cover the complete fixture, metadata projection, duplicate/missing
profiles, unsupported operation/native acceptance/release claims, symbol bounds,
policy contradictions, protected server mediation, sign-in/unavailable rendering,
explicit disabled skeletons and paper denial/absence states. Production build/type
checking, scoped frontend lint, protected backend registry/health reads and the
original paper broker response shape are included in focused verification.

Browser interaction/layout/focus/responsiveness acceptance remains pending after
the earlier policy-blocked launch; no bypass was attempted. No actual native SDK,
terminal, credentials or broker was used. Native alternative adapters remain
unsupported. Operational eligibility evidence, native lifecycle/idempotency,
complete sync/history reconciliation, operator/orphan recovery, independent
review and P04/P12/P13 acceptance remain pending.

No dependency, wire schema, backend runtime behavior or journal layout changes
are introduced. Rollback removes this view/reader while retaining all execution
and observation journals and unresolved custody evidence.
