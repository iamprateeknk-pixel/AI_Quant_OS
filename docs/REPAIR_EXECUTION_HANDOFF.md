# One-shot isolated repair verifier execution

## Delivered execution boundary

`ai-quant-repair execute` prepares the complete immutable candidate package and
the exact bounded suite bytes, then creates and rechecks the pinned, fixed-profile
Linux container. It issues one attached `container start` request for that
container ID only. The owner/snapshot/image/suite/request pins and exact start
arguments are fsynced to a unique start-intent file before the RPC. An existing
intent or any partial execution artifact blocks another start for that owner.

The attached command has a 45-second wall deadline and the existing 64 KiB
combined stdout/stderr cap. Docker container logs remain disabled. The trusted
parent captures streams into owner-named files outside the read-only `/job`
mount, then inspects the exact immutable container ID. A complete receipt is
published only when terminal state, exit code, owner, image and restart count are
coherent. The result is `EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION`
for exit zero, or `EXECUTION_FAILED_AWAITING_REVIEW` for a nonzero exit. Both
states have `execution_verified=false` and `promotion_allowed=false`.

The CLI prints hashes, byte counts, status and artifact names; it does not print
worker output. Artifacts stay under `<repair-root>/jobs` after the staged source
tree is cleaned up. The schema is
[`repair-execution-receipt.schema.json`](schemas/repair-execution-receipt.schema.json).

```powershell
ai-quant-repair --repository D:/AI_Quant_OS --health-store <health.sqlite3> execute <candidate-id> `
  --image-id sha256:<loaded-pinned-linux-image> `
  --suite-sha256 <independently-reviewed-suite-sha256> `
  --suite-file <reviewed-verifier.py>
```

Use the exact owned root from candidate preparation. Optional `--graph-store`
includes the original verified archived invocation input. The suite hash proves
the staged program bytes match the declared pin; it does not prove that the suite
was independently reviewed. The operator must select a reviewed suite and
toolchain before execution.

## Ambiguous outcomes and cleanup

The start intent is an at-most-once boundary. Timeout, transport failure,
interruption, incomplete attached output or failed terminal inspection leaves
the intent without a completed receipt. The same owner cannot start again. The
exact owned container and staged inputs must be reconciled with the existing
`cleanup` command; apparent container absence, missing output or an intent alone
does not establish test success. If output files were written but the final
receipt was not, treat the attempt as uncertain and retain the evidence.

After restart, inspect an owner without loading candidate code or contacting
Docker:

```powershell
ai-quant-repair --repository D:/AI_Quant_OS --root <repair-root> execution-status <owner-id>
```

The command validates the immutable start intent and, when present, the receipt
against captured stream byte counts and hashes. An intent without a complete
receipt is reported as `EXECUTION_OUTCOME_UNCONFIRMED`; the reader never retries
the start. A complete receipt is reported as `EXECUTION_RECORDED`, still
unattested and without promotion authority.

Confirmed completion does not issue an independent certificate, register or
activate the proposed manifest, release quarantine, or authorize shadow/canary
promotion. Full source and regression semantics still require an independent
verifier and its own source-bound contract. A durable evidence-review checkpoint is integrated into the opt-in PAPER API/Temporal worker; independent behavioral
verification, shadow/canary, critical/agent incident resolution, rollback
automation and gated recovery remain open. Quote incident acknowledgement is
available separately through the watchdog. See `REPAIR_TEMPORAL_HANDOFF.md`.

## Verification

The focused repair container/CLI suite passes with fault-injected Docker control
flow and real temporary Git/SQLite sources. It covers exact start arguments,
bounded output and wall time, at-most-once refusal, ambiguous-start retention,
nonzero exit reporting, attached-versus-inspected exit mismatch, closed result
schema and no authority escalation. An opt-in local-engine smoke test runs only a
fixed harmless print program through the pinned image, checks the captured output
and receipt, and confirms exact container removal. It does not execute a real
repair candidate or establish correctness. The latest evidence is recorded in
`docs/BUILD_STATUS.md` and `docs/evaluations/repair-execution-live-0161.json`.
