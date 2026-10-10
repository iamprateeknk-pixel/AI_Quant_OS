# Protected realtime paper operations (0133)

## Delivered scope

Add a full-state snapshot and bounded server-sent event channel for paper safety
state, recorded order inventory and simulated world-state evidence. Mission
Control now displays automatically refreshed paper operations through the
existing private operator session. Its older page summary is explicitly labelled
as the observation read at page load.

| Boundary | Route |
| --- | --- |
| Protected backend snapshot | GET `/api/v1/realtime/snapshot` |
| Protected backend stream | GET `/api/v1/realtime/events` |
| Private browser mediation | GET `/api/realtime/events` |

Sign in through Risk Center before opening Mission Control. Session credentials
remain in the HttpOnly cookie and the fixed server-to-backend header. The web
route accepts no query parameters, validates the configured loopback origin,
refuses redirects and uses no cache. No credentials appear in stream IDs, frames
or client code. Backend access accepts existing direct owner authentication or
an existing session, with audit/health admission preserved.

## Snapshot and reconnect semantics

Every API instance has a random boot epoch and a globally increasing sequence
for its snapshots. Every connection starts with a `reset` containing full state;
later `snapshot` events also contain full state. IDs bind epoch and sequence.
The browser accepts strictly increasing sequences, refuses regressed observation
times and requires a reset before accepting a new epoch. Sequence gaps are legal
because other consumers share the instance counter. No delta replay or persistent
global sequence is claimed; Last-Event-ID does not request historical replay.

Each frame retains its original capture and source times. The lease is at most
three seconds and never extends beyond the original world-state validity. The
server withholds regressed clocks. Proxy and client reject invalid/future/expired
frames, unsafe counters, repeated instrument IDs, wrong paper provenance and
unsupported safety states. Timestamp parsing preserves the original strings and
uses conservative millisecond expiry on the browser.

The UI marks connecting, disconnected, expired and withheld updates. It keeps
last accepted data visibly historical while current state is unknown. A feed
observation cannot acknowledge a pending safety action: commands still need the
original backend receipt. Client subscription cleanup closes the event source;
lease timers are cleared when replaced or unmounted. Browser timer and event-loop
behavior still needs actual browser acceptance.

## Access, lifetime and bounds

Authority is checked before capture and again before every emitted frame.
Revoked/expired sessions and unavailable audit/health state end the stream.
The web proxy validates each bounded event and forwards selected safe fields;
extra/private upstream fields are stripped. Rejected responses, malformed frames,
aborts and stream cancellation release the owned upstream reader.

Limits per backend instance: eight active streams, twenty frames per connection,
one-second sampling, 32 KiB JSON frames, JavaScript-safe sequences and an order
inventory count of at most 100,000. Saturated connections close without emitting
state. The browser reconnects with a two-second retry; the server proxy has a
25-second deadline, a 33,000-byte event bound and a 700,000-byte stream bound.
There is no unbounded queued history or background replay job.

Recorded order count uses a locked SQL inventory count, without loading every
order and fill. It does not certify individual financial records. Safety and
world observations are captured under the existing control lock; this read-only
summary is not a multi-store financial transaction or broker synchronization.
State verification and session checks remain necessary on every sample; load,
multi-worker and deployment behavior require further acceptance.

## Verification and outstanding scope

An actual owned local HTTP server demonstrated reset/sequence frames, a paper
safety change and stream termination after session revocation. It was stopped
and joined at the end of the test. Other checks cover current-state agreement,
restart epochs, source expiry, clock regression, connection capacity/release,
duplicate intent inventory, durable inventory, proxy bounds/cancellation,
private-field stripping and initial unknown-state rendering. The frontend fixture
is checked against the actual protected backend, except its boot epoch.

The machine evaluation records fresh staged-file regression, frontend/build,
format and secret-scan evidence. The additive schema is
`schemas/realtime-snapshot.schema.json`; all older schemas are preserved.

This connects Mission Control's paper operations. Realtime subscriptions across
all fifteen views, order/position/cost/agent detail updates, durable replay,
production load and distributed state, OTel/Grafana, actual feed custody,
browser interaction/visual acceptance and independent critical review remain
pending. The prior browser-launch refusal and uncertain candidate workers remain
preserved. All P00–P13 remain incomplete; no broker, model provider or live capital
is activated.

## Rollback

Revert the increment commit to remove the two backend reads, web stream and panel.
There is no database migration, provider enablement or archive reinterpretation.
The count helpers add read-only interfaces and preserve existing order decoding.
Keep evaluation artifacts and fixtures as historical evidence.
