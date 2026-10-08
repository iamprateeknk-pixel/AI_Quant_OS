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

## Verification and remaining development

See `evaluations/operator-controls-0090.json` for scoped verification, including
real Edge use of a production build against synthetic paper state. The browser
check drops a successful acknowledgement and recovers the durable receipt. It
also checks failed re-authentication, origin rejection, all three controls,
private cookies, mobile overflow and logout. No broker or paid provider is used.

P12 and the full P00–P13 objective remain incomplete. Outstanding P12 work includes
durable browser recovery across page reloads, typed/persisted rejected and pending
decisions, recovery/incident workflow UI, denial audit/rate limits, complete
session lifecycle/resource shutdown, realtime sequence-gap recovery, stale-data
indication, all remaining service views, observability/cost governance, reference
visual regression and independent accessibility/security review. An unconfirmed
action is retained within the current component; record its displayed UUID before
reload. A missing receipt does not prove that a request never executed. Rejected
commands presently return HTTP errors rather than durable rejection receipts.

Use one owned local API process per state directory. Concurrent manually launched
APIs are not a distributed risk-kernel deployment. Hosted authentication, trusted
TLS deployment, external secret management and externally anchored audit remain
release requirements. Restricted live rollout stays gated.
