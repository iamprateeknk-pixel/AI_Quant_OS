# A07 Data Integrity Agent handoff

## Installed behavior

A07 0.2.0 is a PARTIAL/SHADOW mission installed in the local LangGraph supervisor. It validates current paper feed statuses, local bar-content lineage and clock-health declarations. DataHealth uses the shared mission envelope with exact task/version, evidence references, expiry, trace, confidence zero, uncertainty one and mandatory escalation.

Missing, stale, future or untrusted quotes, insufficient/stale history, a missing matching bar hash and measured clock offset above 1,000 ms produce unusable feature findings and advisory quarantine flags. The 1,000 ms threshold is an initial development policy, not a calibrated production bound. Clock measurements are currently UNVERIFIED; a synthetic replay verifies the measured-offset path without claiming an NTP connection. Local lineage proves only consistency with the captured in-process bar hash, not independent vendor lineage.

Health evidence expires five seconds after the diagnostic observation, independently of market-evidence expiry. This allows diagnosis of stale data without presenting it as fresh prices. A07 emits no replacement price. A persisted NO_NEW_RISK incident request recommends review when instruments are unusable; no handler changes safety state or sends a broker order. Existing deterministic market/risk checks continue independently.

IngressHealth continuously counts canonical domain ingestion rejections, duplicates, and schema-rejected quote/bar requests in this process. Counts include invalid quote/bar, future bar, conflicting/identical duplicate bar outcomes, and FastAPI request-validation failures on the two canonical ingress routes. Counters retain only bounded event counts, not request bodies or raw prices. Rejected rows never replace accepted data. Historical rejected/duplicate counts are disclosed and do not automatically invalidate a currently valid accepted feed. Counters reset on restart and identify their process-local retention explicitly. Other routes' schema failures are outside A07's feed-ingress counters.

## Files and use

- integrity_agent.py: validated inputs, diagnostic source adapters and typed mission output.
- ingress_health.py: bounded thread-safe ingestion counters without raw prices/errors.
- api.py: canonical ingestion observations, A07 context selection and handler installation.
- tests/test_integrity_agent.py and tests/fixtures/integrity_baseline.json: API and actual-graph fault replay.

Configure AI_QUANT_PAPER_DB, then create a protected context with agent_id A07, agent_version 0.2.0 and task_id. Execute its context_ref through `/api/v1/paper/agents/execute`. Archived results survive restart and remain advisory. A07_MISSION_DESCRIPTOR.json records the exact permission manifest, owner role, route, memory policy, lifecycle and evaluation suite; it grants no client-controlled authority.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q tests/test_integrity_agent.py tests/test_graph_runtime.py
python -m pytest -q
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
```

## Unfinished scope

Independent vendor/archive lineage, external feeds, real clock synchronization measurements, durable ingestion telemetry, schema-rejection metrics, continuous mission scheduling, incident-path delivery/acknowledgement, downstream quarantine consumption, empirical evaluation and independently approved promotion remain open. The synthetic fault replay is a development baseline, not a holdout or calibration report. No agent promotion is approved.
