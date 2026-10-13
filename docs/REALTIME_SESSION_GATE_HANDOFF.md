# Realtime session gate handoff

## Summary

The browser now checks a shared same-origin no-store authorization probe before
opening the protected realtime EventSource or requesting/reviewing the client
incident journal. The response contains only an authorization boolean. Missing,
revoked, or unavailable sessions do not dispatch those protected client requests;
realtime retries with bounded exponential backoff. Pending checks are invalidated
when the final stream subscriber leaves.

## Verification

- Realtime store regression tests cover unauthorized backoff, authorized connect,
  and cleanup.
- Operator session server tests confirm the probe returns only a no-store boolean
  for missing, valid, and revoked sessions.
- Full web suite: 177 tests passed.
- Next.js production build and TypeScript checks passed.
- Browser check of all 16 navigation routes at 1280 px and 320 px found no console
  errors or horizontal overflow. The anonymous `/incidents` view calls the
  authorization probe and does not request the protected journal.

## Limits

The probe does not replace authorization on `/api/realtime/events`. Browser
accessibility review and all remaining P00-P13 gates remain open.

## Rollback

Revert ADR 0197, this handoff, the authorization route and server helper, realtime
store changes, and their tests together. No persistent data changes are involved.
