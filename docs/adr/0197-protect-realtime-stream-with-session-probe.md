# ADR 0197: Gate realtime connections on operator session state

- Status: accepted for local operator UI
- Date: 2026-10-13

## Context

Mission Control opened its protected realtime EventSource on every page load.
Signed-out users received repeated 401 responses while the browser automatically
reconnected. The client incident-history panel also fetched its protected journal
before checking the operator session, producing a browser-console 401.

## Decision

Before opening the EventSource or reading/reviewing the client incident journal,
the browser calls a shared same-origin, uncached authorization probe. The probe
returns only an `authorized` boolean with HTTP 200 and no-store caching; it never
returns session identifiers or credentials. Unauthorized or unavailable results
do not dispatch the protected client request. Realtime subscriptions retry with
bounded exponential backoff, and unsubscription invalidates pending checks so
they cannot open a late connection.

## Consequences

Signed-out pages do not make client requests to the protected event stream or
incident journal. A valid session opens the single shared stream and enables the
journal request. A revoked session is discovered by a later probe or by the
protected route's own failure response. The probe is a readiness hint; authorization
remains enforced by each protected endpoint. Independent security and full P00-P13
acceptance remain open.
