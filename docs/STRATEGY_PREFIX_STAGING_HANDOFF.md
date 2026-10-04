# Owned inert prefix staging

## Boundary

One staged worker input directory contains only `request.json`: the exact canonical
`CandidateInvocation` bytes reconstructed from a checked validation package and
non-warmup index. Candidate and four SDK files remain encoded data inside JSON;
no candidate/SDK `.py` files are materialized on the host, and their contents are
never imported, compiled or executed there. Only two
strictly earlier closed bars are supplied, without training/full-holdout CSV,
proposed tests, review, plan, current prices or future bars.

The stage receipt lives beside the job directory, outside a future `/job` mount.
It retains package/candidate/invocation identities, request hash/size, exact parent
path and filesystem identities. Execution and promotion remain false. The
caller retains the original receipt SHA-256 independently; restoring a job requires
that pin and the original checked package/index. Rehashing a changed receipt does
not replace the independent pin.

## Operator interface

Create a dedicated ordinary staging root under the trusted checkout/state area.
It must already exist. Use the existing locked Python environment and an existing
checked validation archive:

```powershell
python -m ai_quant_os.strategy_prefix_cli `
  --archive PATH_TO_VALIDATION_ARCHIVE `
  --package-ref validation-package:RETAINED_PACKAGE_HASH `
  --index 2 --root PATH_TO_EXISTING_STAGING_ROOT stage
```

The metadata-only result reports `job_name`, `invocation_ref` and `stage_sha256`.
Keep those values from the initial trusted operation. Inspect later using the same
archive, package, index and exact root path:

```powershell
python -m ai_quant_os.strategy_prefix_cli `
  --archive PATH_TO_VALIDATION_ARCHIVE `
  --package-ref validation-package:RETAINED_PACKAGE_HASH `
  --index 2 --root PATH_TO_EXISTING_STAGING_ROOT inspect `
  --job-name REPLACE_WITH_RETURNED_JOB_NAME `
  --stage-sha256 REPLACE_WITH_ORIGINAL_STAGE_HASH
```

Use the returned `strategy-job-` name verbatim; the illustrative placeholder above
must be replaced. `discard` takes the same arguments as `inspect` and removes only
the exact unstarted request file, empty job directory and its stage receipt. It
does not recurse or remove neighboring jobs or the permanent ownership lock.

## Checks and failure behavior

- Reconstruct the exact original package prefix on every verification. A different
  valid package identity or index is refused.
- Check ordinary directories through every absolute parent component, rejecting
  symlink/reparse metadata and ambiguous `..` paths. Check captured parent/job
  device/inode identity before touching owned inputs. Live operations reject
  changed parents before acquiring a lock; restoration verifies its independently
  pinned parent record before opening the root lock.
- Use the existing nonblocking kernel ownership protocol with the permanent
  `.strategy-owner.lock`; all participating writers must use this protocol.
- Require a single-link bounded regular request, exact original bytes, captured
  file identity/size/time/mode and a read-only file mode. POSIX job mode is 0755;
  Windows checks the read-only file mode, without claiming POSIX permissions or
  a hardened Windows ACL. The stage receipt is at most 64 KiB and the request at
  most 512 KiB. At most 32 job directories are admitted in a root.
- Refuse missing/changed/extra files, even an identical-byte request at a new inode,
  changed root, altered receipt, hardlinks, unsupported names or wrong pins.
- Refuse discard whenever any additional sidecar with that job's prefix exists,
  including a future container ownership record. Absence of a sidecar proves no
  container only under the required cooperative parent protocol.

Writes fsync each file. Stage/request publication and deletion are separate file
operations; crashes or errors can retain partial inputs/receipts. No automatic
repair, recursive removal or partial-cleanup recovery is claimed. CLI failure
omits source and exception details and tells the operator to retain uncertain
inputs. Inspect the retained state rather than assuming deletion succeeded.

The host root/namespace is trusted; identity checks and cooperative locks do not
defeat an administrator or a malicious concurrent filesystem writer. In particular,
Windows read-only mode and path checks are not physical holdout isolation or a
kernel sandbox. Actual Docker bind-path translation, mount admission, read-only
enforcement and prevention of host-path races across create/start remain required.

## Remaining execution

No staging command loads an image, contacts Docker, creates/starts a container or
issues source/isolation/acceptance evidence. The future parent must publish durable
container ownership before any create request, hold the root protocol through
admission/start, inspect complete image/container/mount/namespace/resource metadata,
own deadlines and exact-ID cleanup/recovery, and sanitize bounded output. Actual
Linux enforcement, separate no-holdout proposed compilation/tests, candidate behavior,
independent source attestation, statistical acceptance and release gates remain open.
See ADR 0072, `CANDIDATE_PREFIX_HANDOFF.md` and `STRATEGY_IMAGE_HANDOFF.md`.
