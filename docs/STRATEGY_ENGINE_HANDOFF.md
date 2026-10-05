# Real local strategy engine check

The fixed local Docker Linux engine and the original pinned worker image have now
completed a synthetic full-prefix run. ADR 0079 records the actual default metadata
compatibility corrections; the earlier image/run handoffs retain their original
archive and command pins.

## Repeat the integration

Docker Desktop must be running. Load the original independently pinned archive
using `ai_quant_os.strategy_image_loading load` as described in
`STRATEGY_IMAGE_HANDOFF.md`. Image loading does not execute a candidate.

Use the locked environment, explicitly enable the real-engine gate, and choose a
new evidence directory. Never reuse an existing pytest base directory because
pytest can clear its contents.

```powershell
$env:AI_QUANT_STRATEGY_ENGINE_TEST = '1'
data-local/locked-windows-0055/Scripts/python.exe -m pytest -q tests/test_strategy_prefix_engine.py --basetemp=data-local/strategy-engine-another-new-run
Remove-Item Env:AI_QUANT_STRATEGY_ENGINE_TEST
```

The gate normally skips. When enabled it requires the original local archive at
`data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar`; it never downloads or
substitutes an image. It creates a synthetic development package through the
existing preparation API, then starts proposed source only through the admitted
worker. Host code reads package/source metadata but never imports the candidate.
Each decision gets a fresh container with the established fixed recipe and only
its two closed bars. The complete training/holdout archive and proposed tests are
not mounted into that worker.

The test requires complete distinct container receipts, exact target comparison,
both native cost scenarios, acknowledged removal and released input directories.
A new CLI process must return the same report without changing its bytes. A final
read of the actual engine must show none of the completed container IDs remaining.

## Evidence and recovery

The final focused run is retained under
`data-local/strategy-engine-0079-final/test_real_engine_full_prefix_r0`.
Its run report hash, original package/candidate/image identity, successful container
IDs and verification scope are retained in
`docs/evaluations/strategy-prefix-engine-0079.json`.

The first failed create was never started. Its exact recorded container was removed
through the original ownership/journal cleanup CLI. Its package, request and audit
records remain under `data-local/strategy-engine-0079-first` for investigation.
Any new partial failure must use its own original independent pins and recorded
ownership; do not retry a possibly started prefix or remove shared Docker objects.

The observed synthetic runtime is not an independent isolation or source-behavior
certificate. Proposed compilation/tests, broader target cases, malicious-source
probes, independent attestation, statistical acceptance, production inputs, full
agent missions and promotion/go-live requirements remain open.
