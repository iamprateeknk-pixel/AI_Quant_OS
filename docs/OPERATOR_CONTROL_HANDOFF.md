# Local operator sessions and paper safety controls

## Use

Privately configure `AI_QUANT_OPERATOR_TOKEN` for the backend, rebuild Mission
Control, and start `ai-quant-local`. Open Risk Center, enter the operator access
code, choose a paper action, supply a reason, and re-enter the code. Flatten only
and halt require their exact typed confirmation. Success appears only after the
backend returns its accepted receipt. Flatten only does not liquidate positions.

The local launcher sets `AI_QUANT_WEB_ORIGIN` to its exact dashboard URL, including
custom ports. Manual frontend sessions default to `http://127.0.0.1:3000`; set the
origin explicitly when changing ports. Credential forwarding accepts only a
literal `http://127.0.0.1:<port>` API origin. Browser mutations require the exact
configured Origin and Host. This implementation is for local paper operation.

## Session custody

- Login uses the original access code. A session cannot renew itself. Grants
  expire after fifteen minutes; logout revokes them in the backend journal.
- The backend persists only nonce hashes, session identities, credential epoch
  fingerprints and UTC lifetimes in `paper.operator-sessions.sqlite3`. It verifies
  the complete audit chain and projection before authorizing a session. Rotation
  of the backend code invalidates existing grants. Raw codes/nonces never enter
  that journal, response status payloads, application logs or client bundles.
- The browser receives a host-only HttpOnly SameSite Strict cookie. It is Secure
  for an HTTPS configured dashboard origin; local HTTP is supported. The access
  code is submitted transiently and the password input is immediately cleared.
- At most 128 grants and 8,192 journal events are admitted. Expired grants are
  reclaimed with audited revocations. A full or corrupt journal withholds session
  authority. Original-key emergency controls remain independently authenticated,
  subject to the original paper audit/health checks, including after restart.
- Session journal coherence relies on a trusted local host and filesystem; hash
  chaining alone is not externally anchored tamper-proof storage.

## Commands and receipts

`POST /api/v1/operator/sessions` creates a session using `X-Operator-Token`.
`GET` / `DELETE /api/v1/operator/session` require `X-Operator-Session`.
`POST /api/v1/operator/controls` requires a valid session and fresh
`X-Operator-Reauth`. Its command includes the client action UUID, requested
action, reason, session identity, UTC client timestamp, expected safety state and
typed confirmation. New commands admit at most sixty seconds of clock skew.
Expiry is checked again after acquiring the control lock.

The expected state is compared within the same SQLite write transaction that
escalates paper safety and retains the receipt and audit hash. A request hash
binds the complete command. Identical retries return the original receipt;
changed commands cannot reuse the UUID. The risk kernel changes only after
durable storage succeeds. A checked receipt can be retrieved through
`GET /api/v1/operator/controls/{action_id}` without re-sending a command.

Accepted receipts include previous/authoritative states, policy checks, server
time, action/session identities and a receipt-bound trace. Published command and
receipt schemas match the runtime models. Existing original-key controls retain
their wire format and idempotency behavior. Session callers must re-authenticate
for the legacy controls and paper recovery too. No NORMAL command is exposed.

Authenticated, well-formed commands refused for a stale timestamp, stale expected
state or forbidden safety recovery now return durable REJECTED receipts. They
preserve the unchanged observed state and explicit rejection code. Identical
retries and receipt lookup return that original rejection after restart. Malformed
or unauthenticated requests return HTTP errors and do not gain receipt authority.

The browser retains the exact non-secret command in tab-scoped session storage
before sending it. Reload restores its identity, original timestamp and fields;
it never automatically retries. Use Check receipt to recover an acknowledgement.
Retry reuses the original command and requires the original session and fresh
code. A new session may look up the retained receipt, but cannot rebind an original
command to itself. No code or session nonce is persisted in this browser record.
Unavailable/invalid storage disables commands; an unresolved record cannot be
overwritten. A validated terminal receipt clears only its matching action record.

The backend now reserves the original authenticated command before waiting for the
paper control lock. A checked PENDING receipt is readable during that wait and after
restart. Its state is the observation at reservation time, not confirmation of an
applied change. The terminal receipt keeps the original receipt/trace identity and
checks the reservation binding transactionally. Restart never retries automatically.
The UI retains its checkpoint for PENDING and offers another explicit receipt check.
At most 4,096 historical reservations are retained; no automatic deletion or rollover
is provided. An expired original session cannot be replaced to execute its pending
command; administrative cancellation/archival remains future work.

## Verification and remaining development

See `evaluations/operator-controls-0090.json` for scoped verification, including
real Edge use of a production build against synthetic paper state. The browser
check drops a successful acknowledgement and recovers the durable receipt. It
also checks failed re-authentication, origin rejection, all three controls,
private cookies, mobile overflow and logout. No broker or paid provider is used.

`evaluations/operator-control-recovery-0091.json` records the recovery/rejection
extension: 104 scoped backend checks passed in the working tree and a fresh staged
checkout; ten frontend boundary/checkpoint checks passed. The production Edge flow
recovers a dropped acknowledgement after a page reload and displays a durable
forbidden-recovery rejection. Build/type checking, frontend lint, Python lint,
formatting and staged secret scanning passed. This is scoped verification, not a
new full-repository run or independent acceptance.

`evaluations/operator-pending-custody-0092.json` records 109 scoped backend checks
passing in the working tree and fresh checkout, plus twelve frontend checks.
Actual test-owned waiting/interruption cases prove durable original pending custody;
the real production browser separately receives a simulated historical pending
reply and retains its checkpoint before recovering the real terminal receipt.
Build/type checking, lint and staged secret scanning passed. All 64 prior schemas
remain unchanged. This is local development evidence, not independent acceptance.

P12 and the full P00–P13 objective remain incomplete. Outstanding P12 work includes
pending-action administrative cancellation/archival, recovery/incident workflow UI,
denial audit/rate limits, complete session lifecycle/resource shutdown, stale-data
indication, all remaining service views, observability/cost governance, reference
visual regression and independent accessibility/security review. Tab closure can
discard the browser checkpoint; retain the displayed action UUID for backend
lookup. A missing receipt does not prove that a request never executed.

Use one owned local API process per state directory. Concurrent manually launched
APIs are not a distributed risk-kernel deployment. Hosted authentication, trusted
TLS deployment, external secret management and externally anchored audit remain
release requirements. Restricted live rollout stays gated.
