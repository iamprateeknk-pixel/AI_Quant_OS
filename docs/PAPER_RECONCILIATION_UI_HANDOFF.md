# Paper reconciliation UI handoff

## Summary

The Orders page now offers an explicit comparison between the local paper ledger and the broker's paper-position snapshot. The comparison runs only after an operator checks the acknowledgement and submits the form. It uses the protected operator session, same-origin route, fixed backend endpoint, bounded response, and no-store requests. A successful response is strictly parsed before the page shows matched totals, per-instrument differences, or differing fill references.

The backend comparison records an audit event and can escalate safety to `NO_NEW_RISK` when positions disagree or the comparison fails. The page explains that consequence beside the acknowledgement. It never runs during page load. An unavailable or malformed response is presented as an unknown outcome, not as a clean match.

## Verification

- Parser, server boundary, confirmation, and server-rendered panel tests pass.
- Full web regression: 167 tests passed.
- Next.js production build and TypeScript checks pass.
- Browser visual and accessibility acceptance remain open.

## Limits

- The current backend comparison contract exposes EURUSD. The parser supports the declared instrument set and displays only instruments returned by the backend.
- This is an audited paper-ledger comparison. It does not create, cancel, or reconcile orders with a live broker.
- P12 and the full P00-P13 acceptance remain in progress.

## Rollback

Revert the Orders panel, same-origin route, server proxy, parser/tests and this handoff together. The UI does not persist state; comparison audit records and safety escalation already produced by an operator action are backend records and are not removed by a UI rollback.
