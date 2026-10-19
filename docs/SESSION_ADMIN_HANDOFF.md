# Settings owner-session administration

Increment 0104, 2026-10-09. Partial P10/P12 delivery. All P00-P13 phases and live
release gates remain incomplete.

## Operator workflow

Settings now shows the actual local owner session inventory through the current
server-owned session cookie. Sign in there with the operator access code. The
bounded table shows session IDs, UTC creation/expiry, and Current/Active/Expired/
Not yet valid status from one checked server observation. It displays up to the
existing 128 retained grants for the current credential epoch. No device, IP or
location is inferred. Older credential epochs and revoked grants are absent from
this current inventory; original journal records remain retained.

Select exactly one session, confirm the selection and re-enter the access code.
The selected session loses authentication immediately after a committed receipt.
Self revocation explicitly signs out the current browser; a new owner login is
required. Ending a session cannot cancel a previously admitted paper action.
Browser credentials are cleared from forms before I/O and never enter persistent
state. The HttpOnly cookie is cleared only for a verified self-revocation receipt.

A changed list returns conflict and requires refresh/reselection. Unavailable or
unverified responses never claim revocation. A lost response can mean the session
was revoked; refresh the inventory/sign in again before attempting another action.
The timer is a display convenience from the backend observation and remaining
lifetime. Current backend authentication is authoritative at every request.

## Admission, journal and contracts

- GET /api/v1/operator/sessions requires a valid current session, independently
  repeated under the store's transaction before returning metadata. Direct-token
  reads cannot impersonate the Current row without an actual session grant.
- POST /api/v1/operator/sessions/revoke requires that session, fresh owner credential,
  one UUID4 target and exact original inventory head. Actor, epoch, time, head and
  target are rechecked inside the same exclusive transaction as the audit append
  and grant deletion. Concurrent changes cannot revoke against a stale list.
- SESSION_ADMIN_REVOKED retains actor/target identity, target nonce digest, UTC
  time and original request head. Replay verifies source-chain previous head,
  original actor lifetime/epoch, target identity and actual projection. No raw
  nonce, credential or credential fingerprint appears in public DTOs.
- A failed projection write rolls back its audit event. Corrupt session history
  withholds grants/administration while independent direct-token emergency paper
  safety controls remain available when their own journals are sound.
- New owner login retires expired or invalidated prior-credential grants through
  the existing audited revocation event before capacity admission. Future-clock
  grants are preserved rather than revoked with a timestamp preceding creation.
- Strict SessionInventory and SessionRevocationReceipt response models and new
  JSON Schemas bind UTC/lifetime, unique/current identity, observed status, source
  metadata and self-revocation scope. Existing schemas remain unchanged.

The Next server forwards credentials only to the fixed local API; the browser
calls same-origin Next routes. Request origin/host, size, exact fields, target/head
and receipt identity are validated. Public projection occurs before the inventory
crosses into client component props. Upstream errors and extra private fields are
withheld. Source counts are journal events, not login analytics or total users.

## Compatibility and remaining work

No SQL table migration is introduced. The new administrative event requires the
new journal reader; older code intentionally rejects it. Preserve this reader for
rollback or use a compatible release. Do not delete/reseed history to make older
code accept it. Existing ordinary sign-out/expiry events remain readable.

Read-only metadata and single-owner revocation are implemented. A production-build
browser acceptance run now covers owner sign-in, inventory of a second independent
session, explicit single-target confirmation, fresh credential entry, receipt
display, refreshed inventory, and denial of the revoked session after reload. See
`evaluations/mission-control-session-admin-browser-2026-10-19.json`; screenshot
artifacts are locally ignored under `apps/web/output/playwright/`.

Display/timezone/notification preferences, integration administration, environment
feature flags, broader session/role policy, retention/export, other authenticated
workflows, independent security review and broader browser acceptance remain
pending. The full release suite has not passed acceptance.

See evaluations/session-admin-0104.json for fresh focused verification. No paid
provider, external broker, candidate SDK or proposed source was executed.
