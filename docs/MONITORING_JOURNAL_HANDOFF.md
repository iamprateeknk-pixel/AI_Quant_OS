# Monitoring incident journal handoff

The monitoring read route records validated local Prometheus rule transitions in
an append-only SQLite audit chain beside the configured paper database. Repeated
reads with an unchanged rule state do not add events. Unavailable observations
mark an active episode as not currently observed; only a later available
observation can record it inactive. New Prometheus active times create separate
episodes.

The session-protected incident history route returns fixed pages of50 episodes,
source rule, observed timestamps, state, runbook path, the journal head and any
review receipt. Continuation pages require the same head; a changed journal
returns a conflict and the view refreshes from page one. The acknowledgement
route requires the current journal head, a unique action UUID, and a current
operator session rechecked before commit. Same-action replay by the original
actor is idempotent. A committed receipt records review only; its
`control_authorized` field is always false and it cannot change risk, orders,
HALT, quarantine, or recovery state.

The Incidents page reads history through same-origin server routes. The HttpOnly
operator token stays on the server. The client decoder accepts only the fixed
page and receipt fields; review controls are disabled while a request is
pending. Acknowledgement is explicitly described as review and never implies
resolution or notification delivery. The prior 0158 receipt schema remains
unchanged; the paged history contract has a separate additive schema.

The journal has an 8192-event and 4096-byte-per-event bound. Existing damaged or
incomplete metadata is withheld without reinitialization. The source's
`notification_verified` and `independently_attested` claims remain false. There
is no external notification delivery, engine-stopped detection, cross-source
atomic snapshot, or production custody attestation.

Verification covers the supplied persistence, replay, revocation, corruption,
capacity, authenticated HTTP, restart, and native source API cases. The page
contract, stale-head paging, server boundary, client decoder and complete web
test set are checked; Next production build passes. Browser interaction,
visual/accessibility acceptance, production multi-process storage, and the
remaining P00–P13 gates are still pending.
