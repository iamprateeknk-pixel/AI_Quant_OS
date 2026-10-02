# Durable repair container creation records

The trusted parent commits a schema 1.2 `CREATE_INTENT` after publishing and
verifying the ownership lease, before sending the single create request. The
event binds the owner, canonical lease (source/suite/request/image/root identity)
and exact ordered creation arguments. It contains no candidate source or suite
payload. Argument and serialized event sizes are bounded. The event shares the
existing audit journal, root ownership and SQLite FULL commit configuration.

A successful full-ID response is retained as `CREATE_ACK` before inspection or
consumer admission. This records the response, not successful confinement or
behavioral verification. Created-container metadata and exact job inputs must
still pass their existing admission checks before yielding an admitted handle.
No container is started and no candidate/verifier program is executed.

## Interruption and cleanup

Failed intent persistence sends no create or cleanup RPC and retains the
prepublished lease and inputs. Read-only prerequisite queries precede this point.
If the request might have been sent, restart
cleanup checks the retained creation arguments against the current independently
constructed request before daemon access. It never repeats creation.

An acknowledged creation permanently pins the full ID for subsequent removal.
A container with the expected name, owner and image but a different ID cannot
replace it. Without a committed creation acknowledgement, exact present ownership
can still be inspected and recorded for cleanup only. A failed acknowledgement
commit does not admit a consumer; confirmed cleanup can close that attempt.
An absent container does not prove that an uncertain create cannot finish later.
Its lease, journal entry and inputs remain unresolved.

Creation events precede any removal intent. When present, their lease and
acknowledged ID must match later removal evidence. The journal reserves up to five
events per owner: `CREATE_INTENT`, `CREATE_ACK`, `INTENT`, `REMOVED`, `CLEANED`.
Capacity counts distinct owners across all events, with a maximum of 1,000 owners
and 5,000 events. Owners without a committed acknowledgement may finish with four
events. Unfinished creation also blocks new staging, including without a removal
intent. Every admitted owner retains room for cleanup at capacity.

Existing schema 1.0 removal and schema 1.1 terminal events retain their bytes.
Legacy cleanup without creation events remains supported, but supplies no
retrospective proof of a creation request. Changing current creation options can
prevent an old request from passing recovery admission; a reviewed migration or
versioned policy is needed before such upgrades. Older readers reject schema 1.2
events. Preserve journal, root lock, receipts and inputs before rollback.

## Verification and remaining work

Seven added cases use actual Git, SQLite and files with a substituted daemon
boundary. They verify committed exact intent at the create call, reopened response
identity and five-event completion, zero create/cleanup RPCs after intent failure, cleanup without
admission/resend after acknowledgement failure, idempotence/capacity/ID binding,
image mismatch against the published lease, and restart refusal for changed
request options or replacement IDs. Review moved journal assertions outside
expected retained-job cleanup errors so they cannot be masked. The image mismatch
then failed independently and was fixed. Fresh results are in `BUILD_STATUS.md`.

These are local storage/control-flow checks, not live Docker, power-loss or kernel
confinement evidence. Trusted local paths, cooperating root-lock callers and the
fixed local daemon remain assumptions. A local hash chain does not defend against
an administrator rewriting all evidence or constitute a signed behavioral
certificate. Staging interruption before lease publication, definitive daemon
completion fencing for ambiguous RPCs, rotation, versioned policy migrations,
reviewed offline image and worker, independent behavioral checks, Temporal repair
stages, shadow/canary, quarantine closure and full release gates remain open.
