# Durable strategy container ownership

## Implemented scope

The trusted parent can publish one owner for an existing exact prefix job and
record a durable `CREATE_INTENT` before a future Docker create request. The sidecar
`JOB_NAME.container.json` stays outside the worker mount and binds the absolute
job path/name, original invocation/stage hash, intended image ID and worker source
hash. It keeps execution and promotion false. This reservation does not inspect
an image, contact Docker, create/start/remove a container or execute candidate code.

`reserve_strategy_container(job, package, image_id=..., worker_sha256=...)` holds
the new public `job.admitted(package)` context. That context checks original inputs,
holds the permanent kernel root lock and checks inputs again on normal return.
The journal is validated before sidecar publication; the file is exclusively
created/fsynced, then the audited create intent commits. A failure leaves uncertain
sidecar/inputs retained. A second owner cannot reserve the same job, including
through the journal directly.

The journal file is `strategy-containers.sqlite3` in the trusted staging root. Its
SQLite transactions use synchronous FULL and the existing local audit chain.
Existing records require valid integrity metadata and zero legacy adoption; corrupt
or missing heads are refused without reinitializing history. Record schema,
canonical JSON, exact ownership, immutable IDs and state transitions are rechecked
on every read/append. History is bounded by 4,096 owners, 16,384 events, 8 KiB per
event and a 128 MiB ordinary single-link journal file. Capacity includes removed
owners; automatic retention/rotation is not implemented.

## Caller contract

The execution controller must hold `job.admitted(package)` while opening/using
`StrategyContainerJournal` and issuing future daemon requests. Standalone journal
methods assume that cooperative root protocol; they do not acquire a second lock.
The host namespace is trusted. This is local tamper detection, without independent
immutable storage or protection from an administrator rewriting all local history.

Before any future create RPC, the controller must independently inspect the exact
loaded image/engine, reconstruct the fixed complete create arguments and retain
their binding. Reservation records intended pins, without granting image admission.
The complete create recipe and actual container admission are not implemented here.

Supported record transitions:

| Prior state | Next state | Required trusted observation |
| --- | --- | --- |
| none | CREATE_INTENT | Exclusive ownership published for exact prepared job |
| CREATE_INTENT | CREATE_ACK | Exact immutable container ID from successful create |
| CREATE_INTENT or CREATE_ACK | REMOVE_INTENT | Independently inspected exact owner/name/image/ID |
| REMOVE_INTENT | REMOVED | Exact owned daemon removal acknowledgement |

The cleanup branch from CREATE_INTENT supports a future ambiguous-create recovery:
inspect only the prepublished unique name, verify its full ownership and use its
immutable ID. Unknown absence is not removal proof. The journal performs none of
those Docker actions itself; caller-supplied IDs are statements, without evidence
of physical creation/removal. Exact repeated acknowledgements are idempotent data
operations and never issue/reissue RPCs. Historical returns do not reactivate an
owner; consult `current(ownership)` before each future action.

## Restart and retained state

Keep the initial sidecar SHA-256 independently. Restore the prepared job using its
original package/index and stage pin, then use
`load_strategy_container_ownership(job, package, ownership_sha256=...)` to validate
the sidecar and exact audited binding. Altered pins, job/path/invocation/image/source
bindings, foreign roots, missing intent and conflicting/out-of-order IDs are refused.

Even after a recorded REMOVED state, the ownership sidecar and input directory
remain present and the older unstarted-discard command refuses them. This prevents
metadata acknowledgements alone from deleting inputs. Actual owned daemon cleanup,
sidecar release and partial/restart input recovery require the next controller.
No automatic cleanup or daemon mutation is performed by these library functions;
opening a previously missing journal can initialize its empty local database.

The two retained JSON Schemas describe ownership and records. Cross-field path,
state/ID and chronological/history checks also require model/journal validation.
Do not use schema validity or recorded acknowledgements as an execution, isolation,
source-test, A10 acceptance or promotion certificate.

## Remaining work

The Docker Linux endpoint remains unavailable. Complete immutable image/create
recipe binding, container mount/namespace/resource admission, owned create/start/
deadline/kill/remove/recovery, actual kernel enforcement, source staging races,
separate no-holdout proposed compilation/tests, independent behavior/source
attestation, statistical acceptance and every production gate remain open.
See ADR 0073, STRATEGY_PREFIX_STAGING_HANDOFF.md and STRATEGY_IMAGE_HANDOFF.md.
