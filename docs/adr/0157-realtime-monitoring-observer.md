# 0157 — Gate periodic protected monitoring reads on the existing realtime channel

## Decision

Keep monitoring I/O out of deterministic safety/world captures. Share the existing
client channel as the update trigger and retain independent protected15-second
monitoring reads/source timestamps. Use one outstanding read, a four-second
caller deadline and8192-byte decoded body ceiling; delegate auth to the existing
server/private backend boundary. Select initial props before browser serialization.
Invalidate original work on disconnect/epoch/deadline/unsubscribe; reject late
responses and timestamp regressions. Expire the original observation after45
seconds without lease extension from newer stream frames or identical responses.
No new source stream/backend worker, schema/dependency change or control exists.

## Evidence and limits

Initial six tests failed on missing store. Stronger expiry tests retain identical
original observations through continued current frames. A microsecond test's
first fixture had evaluation after its rewritten observation timestamp, so it
was correctly withdrawn; fixture ordering was corrected, report retained.
An identical-callback test exposed premature unsubscribe cleanup; independent
subscription wrappers fixed it, with original red report retained. Actual shared
store and private route/read tests plus the full staged regression/build are
recorded in evaluation0157. No backend safety lease/read timing is modified.

Periodic protected reads gated by SSE are not native pushed monitoring events or
an atomic cross-source observation. Rule clearing is not acknowledgement,
notification delivery or trading recovery. Durable incident/notification/engine
health, private production/service/Linux/resource/retention/custody and independent/
browser/full P00–P13 gates remain incomplete. Unknown candidate/Docker custody
and prior browser refusal remain preserved.

## Rollback

Revert incident live observer/private GET route, explicit shared channel exports,
selected initial props/live panel and tests/docs. Existing manual panel remains.
No backend/database/schema/dependency/order-state migration, reset/retry,
notification, extra native process or automatic service install is needed.
