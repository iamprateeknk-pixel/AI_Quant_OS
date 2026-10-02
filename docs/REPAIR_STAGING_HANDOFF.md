# Recovery of source-bound orphan staging

New jobs publish `<job-name>.stage.json` outside the candidate mount before the
first input write. Schema 1.0 binds the original job directory device/inode and
path, source snapshot, suite and request hashes. Publication is exclusive and
flushes/fsyncs the receipt. The trusted parent retains that original root identity
through writing and verifies the receipt before yielding the complete input tree.
No source or verifier program is imported or executed during preparation.

`ai-quant-repair cleanup-staging <candidate-id> --stage <receipt> --suite-file
<program> --suite-sha256 <hash>` reconstructs expected bytes from the independently
verified candidate snapshot and suite. Supply the same optional `--graph-store`
when the original job included archived replay. Existing repository, root and
health-journal arguments apply. There is no image argument and no daemon call.

The command owns the local root across admission and deletion. It refuses any
pending container lease or unfinished container journal outcome. Receipts must
be inside the selected root, canonical, bounded regular single-link files with
no reparse points. Every remaining source/request/program/replay file must have
its exact expected bytes and identity; undeclared files/directories, changed roots,
hard links and changed receipts reject deletion. Missing inputs are accepted only
for cleanup. Surviving inputs are deleted before the verified receipt is released;
success is printed only after both operations. Promotion and execution verification
remain false. New staging stays blocked while orphan inputs/receipts remain.

Ordinary cleanup also removes the staging receipt after input deletion. Owned
container cleanup does so before its terminal journal commit. Historical jobs
without staging receipts still use their existing container-recovery protocol.

## Evidence and limits

Eleven cases verify real Git/source jobs/files and a fresh CLI process: receipt
binding and normal removal, complete and partially deleted orphan recovery, changed
bytes/receipt/outside receipt/hard-link refusal, pending leases and unresolved
creation without a lease, and receipt publication before the first source write.
The initial checks failed for missing receipts and absent recovery support before
implementation. Full verification is recorded in `BUILD_STATUS.md`.

This is cleanup evidence on a trusted local filesystem with cooperating OS-lock
callers. It is not a signed certificate or hostile-filesystem defense. The window
between allocating an empty directory and publishing its receipt remains open;
such an orphan cannot be adopted automatically. Torn or changed surviving input
bytes, corrupt/missing receipts and uncertain container outcomes remain retained
for review. Receipt fsync configuration does not prove power-loss recovery; source
file/directory persistence and actual process-death interruption need further
evidence. A missing receipt is not permission to remove a path. Repeating a command
after its receipt has been removed rejects instead of fabricating completion.
Non-root input permissions across restrictive POSIX umasks, live Docker confinement,
the reviewed offline worker/image, behavioral certificates, Temporal repair stages,
shadow/canary, quarantine closure and the broader release gates remain pending.

Before rollback, stop active owners and preserve staging receipts, pending leases,
the journal, lock file and inputs. Older cleanup can leave new staging receipts
behind; use a reviewed recovery procedure rather than deleting them blindly.
