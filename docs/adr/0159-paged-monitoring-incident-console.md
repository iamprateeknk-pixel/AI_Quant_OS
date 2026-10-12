# 0159 — Expose bounded monitoring history and review receipts in Incidents

## Decision

Add a fixed 50-episode page to the protected local journal history route. Every
continuation page must carry the journal head returned by page one; a changed
head returns a conflict and requires a fresh first page. Keep the original
0158 receipt schema unchanged and describe the additive page DTO in its own
schema.

Expose history through same-origin server routes that read the existing HttpOnly
operator session and pass it only to the local backend. The browser receives a
selected page and fixed review receipt. A review action requires same-origin
POST, exact fields, the page head, a fresh action UUID and the current operator
session. The interface labels acknowledgement as operator review only; it does
not claim resolution, notification delivery, or authority over paper controls.

## Evidence and limits

The paged route test validates bounds, stale heads and the additive page schema.
Frontend parser and server-boundary tests cover selected fields, private session
handling, fixed same-origin routes, origin checks, body limits and receipt
authority. The complete web test set and production build pass. Browser
interaction and visual acceptance were not performed.

## Rollback

Revert the paged history contract, same-origin history and acknowledgement
routes, Incidents journal panel, tests, page schema and handoff update. The
0158 durable journal and protected receipt endpoint remain usable without this
browser surface.
