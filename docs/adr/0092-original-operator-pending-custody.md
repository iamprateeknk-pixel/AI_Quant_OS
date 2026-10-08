# 0092: Retain original pending paper-control custody

Date: 2026-10-09

## Decision

Reserve the authenticated original command before acquiring the paper control
lock. Persist a bounded typed PENDING receipt and its audit hash atomically in
the paper database. Bind it to the action, original session and full request
fingerprint. Preserve the receipt/trace identity when publishing a terminal
accepted or rejected outcome. Terminal publication checks its original reservation
within the same transaction as the terminal receipt and, when accepted, safety
state mutation. Earlier accepted/rejected schemas remain unchanged.

PENDING means reserved custody, not applied safety. The recorded state is the
observation at reservation time. Read-only lookup works while mutation is waiting
and after restart. Restart performs no execution or automatic retry. An explicit
retry must retain the original command identity and session, re-authenticate and
pass current admission. Changed requests conflict; altered/missing original
reservation projections withhold execution. Terminal outcomes remain immutable.

The browser validates the pending outcome, retains its existing non-secret
checkpoint and prompts another explicit receipt check. It does not clear the
checkpoint or show success for PENDING. Accepted/rejected receipts remain terminal.

## Limits

At most 4,096 historical reservations are admitted. They are retained after terminal
publication; there is no automatic deletion, archival rollover or new ID reuse.
Hash chaining provides local coherence under the existing trusted-host assumptions.
Pending metadata does not attest that a process is running or that a safety change
has or has not happened since the reservation observation. A missing receipt cannot
prove that a request was never received.

Expired-session pending actions require further administrative resolution work;
a new session can inspect the receipt but cannot rebind/retry the original command.
Cancellation/archival controls, denial auditing/rate limits, session lifecycle
shutdown, realtime/workflow UI, governance and independent release acceptance remain
open. Paper-only scope and the complete P00–P13 objective remain unchanged.
