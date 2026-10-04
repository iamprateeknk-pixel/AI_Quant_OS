# Owned unstarted strategy container controller

Follow-on increment 0075 adds an explicitly invoked `execute` command to the same
operator module. The sections below describe increment 0074's create/cleanup scope;
see STRATEGY_PREFIX_EXECUTION_HANDOFF.md for current start/output/custody semantics.
## Implemented scope

The operator entry point is `python -m ai_quant_os.strategy_container_cli`.
It connects the original validation archive, independently pinned prefix job,
verified static image handoff and durable ownership journal. Commands are
`prepare`, `create` and `cleanup`. There is no start/run/exec command and no
worker or proposed source execution in this implementation.

Preparation rechecks the original request, independently verifies the archive
and currently loaded image/engine, reserves ownership, then exclusively publishes
`JOB_NAME.create.json` outside the worker mount. The bounded canonical recipe binds
all create arguments, complete ownership, image handoff and the pinned base image's
inherited environment. It returns the initial ownership and create hashes.
Retain those hashes independently; do not derive trusted pins from mutable files
at recovery time. Recipe storage is internal canonical JSON, reconstructed exactly
on every use, rather than a transferable execution certificate.

Creation restores/verifies those bindings while holding the permanent root lock.
It re-inspects the archive/image and engine, compares original archive/image/source
identity and inherited environment, then exclusively publishes/fsyncs
`JOB_NAME.create-attempt.json` before one create request. A pre-existing attempt
marker prevents another create request, including after an ambiguous failure.
Failures retain sidecars, request and ownership. Even a marker publication failure
can leave a partial marker; do not delete it to retry.

The fixed recipe uses the unique prepublished name, immutable local image ID,
`--pull never`, read-only root and one read-only `/job` bind. It specifies UID/GID
65534, fixed workdir/entrypoint/program, no network/IPC, private cgroup namespace,
all capabilities dropped, no-new-privileges, 512 MiB memory with no extra swap,
one CPU, 64 PIDs, four process/file limits, a 64 MiB noexec/nosuid/nodev scratch
mount, no logs, no healthcheck and no restart. No candidate/SDK/test directory
is mounted from the host: the original request contains encoded inert source.
Docker's create command creates without starting; see the
[official create reference](https://docs.docker.com/reference/cli/docker/container/create/).

## Created-state admission and cleanup

Successful create stdout must identify one immutable 64-hex container ID. The
journal records that acknowledgement before independent container inspection.
Inspection requires the exact name, ID, image and full labels, actual fixed command,
user/workdir/environment/healthcheck, created/nonrunning state, kernel/resource
advertisements, mount inventory and host recipe. Active/unknown configuration is
refused; enumerated additional defaults are checked with canonical JSON so boolean
and integer substitution cannot pass. The common established resource predicates
are reused after separately validating the actual strategy command and labels.
This is created metadata admission, without evidence of a running kernel or
candidate behavior. Actual engine-specific default/mount compatibility is unverified.

Cleanup first verifies the original job, independent recipe/ownership hashes,
exact attempt marker and current audited owner. It checks the original engine,
then inspects only the known immutable ID or, after an uncertain create, the unique
prepublished name. Foreign name/labels/image/ID or unknown absence refuses mutation.
After recording REMOVE_INTENT, it removes only that independently inspected ID.
Only an exact successful removal acknowledgement permits a REMOVED record.
A persisted REMOVED record avoids another daemon request; it remains local trusted
bookkeeping, without independent attestation. Removal failure/uncertainty retains
all inputs and ownership. No background retries, shared-image deletion or namespace
cleanup is performed. A later cleanup request can reconcile a still-present exact
owned container; absence after an unrecorded removal is not adopted as proof.

All sidecars and the original request remain after REMOVED. The old unstarted
job discard still refuses these sidecars. Automatic partial-job recovery,
sidecar release and input deletion are not implemented here.

## Operator inputs

Every command requires `--validation-archive`, `--package-ref`, `--index`, `--root`,
`--job-name` and independent `--stage-sha256` before the subcommand. `create` and
`cleanup` also require initial `--create-sha256` and `--ownership-sha256`.
`prepare` and `create` additionally require `--image-archive`, `--base-metadata`
and all five independent image/source pins from STRATEGY_IMAGE_HANDOFF.md.
Use `--help` on the module/subcommand for exact argument syntax. Outputs contain
bounded control metadata; errors are generic and do not echo encoded source or
raw daemon errors. Existing original archives must be ordinary single-link files;
a missing archive is refused without initializing it.

Library entry points are `prepare_strategy_container`,
`restore_strategy_container_plan`, `create_strategy_container` and
`cleanup_strategy_container`. A returned string ID grants no execution capability.
The frozen plan is trusted local control data, not a security boundary against an
administrator or caller rewriting all pins/history. Files are exclusively created
and fsynced and operations hold the cooperative kernel root lock. Publication spans
filesystem/SQLite transactions and can retain partial state; process restart is
covered, but power-loss durability of new directory entries and malicious host
namespace writers are not certified.

## Remaining work

The fixed Docker Linux endpoint remains missing. No real image import, container
create/remove/start, proposed source compilation/tests or candidate execution has
been verified. Running container/kernel admission, at-most-once start reservations,
attached bounded output, per-decision deadlines/kill/removal, restart reconciliation
of execution/output, sidecar/input release, physical holdout isolation, independent
source/behavior attestation, statistical acceptance, A10 acceptance and every
production gate remain open. See ADR 0074 and docs/BUILD_STATUS.md.
