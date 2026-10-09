# ADR 0104: Local owner session administration

Date: 2026-10-09

## Decision

Connect Settings to a bounded, verified current-credential session inventory and
single-target revocation. Require current session and fresh owner credential;
commit actor/time/epoch/head/target admission, audit append and projection deletion
in one exclusive transaction. Record an administrative revocation event with
original source head and actor/target identity. Preserve previous session events.

Declare strict public metadata/receipt contracts, project before client props and
validate same-origin requests and fixed local upstream routing. Self revocation
clears the HttpOnly cookie only on a matching committed receipt. Unknown outcomes
require observation rather than assuming success. Rotate credentials without
allowing invalid prior-epoch grants to consume all issuance capacity.

## Consequences

This is local single-owner administration, with no risk, broker or capital
configuration authority. Public metadata omits nonces and fingerprints; devices
and locations are unavailable. Critical direct-token controls remain independent
of a failed session journal.

Older readers reject the new event; retain a compatible reader for rollback and
never reseed history. Preferences, integrations, feature flags, broader policy,
retention, independent security/browser acceptance and full release verification
remain pending. All P00-P13 phases remain open.
