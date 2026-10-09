# Saved agent execution explorer

## Operator workflow

Traces now opens `/traces`, a protected list of saved paper/sandbox graph runs,
ordered by original execution ID. Each row opens its actual saved timeline at
`/traces/{run_id}`. The existing dynamic route name remains `[trace_id]` for URL
compatibility, but its value identifies a saved graph run; a missing old demo
link shows a missing-run outcome. No synthetic trace is supplied.

The list includes agent/version, task, original outcome/scope and context observation
UTC. Context time is not represented as execution time. The detail view renders
actual recorded transition times, original audit sequences, explicit timing gaps
and reservation interval meaning. References/fingerprints are available in an
optional details section. Source and output payloads are not included.

Sign-in links to Settings, where owner session administration/login is available.
Empty archives, an empty final page, missing/invalid runs, invalid pagination,
unavailable evidence and changed snapshots have specific recovery states. Next
links retain the original archive head; stale lists must be reloaded. Links disable
prefetch. Loading exposes status/busy semantics. Long references wrap; wide tables
use the existing scroll container. Actual responsive/focus acceptance remains open.

## Backend and session boundary

`GET /api/v1/agents/graph-runs` requires current operator admission. Maximum page
size is 25; archive capacity is 10,000 graph records and 30,000 audit events. The
reader checks the full retained chain and projection identities, then verifies
original records for the page. Public summaries bind original run/context/manifest
and stable trace identity. A later page requires its exact original head and a
retained cursor. Unknown cursor 404, changed head 409, invalid request 422, damaged
storage 503. The existing individual run route remains available, including run
IDs such as `page`; no ID is reserved for the new collection endpoint.

The collection and timeline are archival reads with no graph/source dispatch,
quota reservation or order mutation. The readonly archive reader is supported.
No SQL migration or prior schema rewrite. The server mediation uses fixed local
paths, no-store/error-on-redirect/time-bound fetch, a bounded response and only its
HttpOnly session credential. Client input cannot choose a host, endpoint or header.

Frontend parsers project only public fields, bind the requested run/page cursor/head,
check ordered identities/counts/events and validate UTC timestamps and interval
claims at microsecond precision using exact integer arithmetic. Missing/regressing
observations withhold elapsed time. These consistency checks do not independently
attest archive ownership, source integrity or current context/permission eligibility.

## Verification and pending acceptance

See `evaluations/trace-explorer-ui-0107.json` for original staged fingerprints,
fresh backend scope and frontend checks/build/type/lint evidence. Initial tests
failed for the absent collection, parser/panels and mediated reads. Production
TypeScript initially rejected BigInt literal syntax under the existing compiler
target; exact arithmetic now uses BigInt constructors without changing that target.

The office-web-ui-system skill requires actual browser inspection. The earlier
preview launch was blocked by policy; this increment does not bypass or retry that
launch. Static React rendering and production compilation are recorded separately
from pending browser keyboard, layout, focus and responsive acceptance.

Parent/child causal navigation, node/tool/source spans, market-intent-risk-order
correlation, filters/search, OTel/export/retention and complete quality/latency metrics
remain unfinished. No execution, retry, recovery, promotion or risk action is added
on these pages. Full P00-P13 development/release acceptance remains incomplete.
