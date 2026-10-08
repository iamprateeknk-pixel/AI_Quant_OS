# Health timeline UI handoff

Date: 2026-10-09

## Implemented

`/incidents` reads the protected paper health service through the existing
server-owned operator session. The nonce stays in the HttpOnly cookie/server
transport, and forwarding still requires the literal local API origin, no redirects,
no caching and a bounded timeout. Only health reads admit the larger 128 KiB
response bound; credential and control request/response defaults remain 4 KiB.

The view distinguishes missing sign-in, disabled monitoring, restored restrictions,
monitoring/error observations, unavailable responses and changed journal snapshots.
Disabled monitoring has no inferred zero incident count or health verdict. It shows
last scan and its age where available, retained quarantine versions, and links to
the exact registry version and authoritative Risk Center. These are observations
at the displayed fetch time; refresh is explicit, not continuous monitoring.

The new protected `GET /api/v1/paper/health/incidents/page` endpoint returns bounded
metadata pages, newest detection first with incident identity breaking timestamp
ties. One SQLite read transaction verifies the entire original journal, including
off-page records, and binds the result to its head hash. Continued pages require
that original hash; changed snapshots return 409, malformed bounds return 422 and
integrity/storage failure returns a sanitized 503. Original list/detail endpoints
retain their wire shapes. The health status endpoint now uses one observation
instead of mixing two separate reads.

The frozen `HealthTimelinePage` response appears in OpenAPI and its exported JSON
schema. Incident contracts bind the original failure taxonomy, UTC timestamps,
safety/quarantine policy and manifest pins. The frontend validates and projects
metadata and page bounds before rendering. Raw captured evidence is never returned
by the page endpoint or displayed in this view. Incident required safety is historical;
the current risk state is not inferred from a row. No scan, recovery or capital
controls are introduced.

## Verification

- Missing paging, typed OpenAPI response, coherent status and malformed frontend
  behavior were reproduced before their implementations/fixes.
- All 20 frontend unit checks passed. Production build/type checks and lint passed.
- The affected health, journal, watchdog, operator session/controls/receipts and
  paper API regression scope passed 85 checks in 15.38 seconds.
- Installed Edge exercised the final production UI against actual local services
  with 27 explicitly synthetic retained incidents: sign-in, 25/2 record pages,
  changed-head rejection, keyboard inspection, 390px layout, hidden raw evidence
  and nonce, and sign-out. A separate empty owned database proved the disabled view
  does not fabricate an incident-free or healthy state. Browser page errors were empty.
- The first browser attempt used an incorrect test database environment variable;
  the API correctly returned DISABLED. The harness was corrected to `AI_QUANT_PAPER_DB`.
  A second harness failure reloaded before first-page navigation completed; captured
  URL/state proved the race, and the test now waits for restored page data. Assertions
  were preserved. Final browser checks passed after the final typed API and UI edits.
- Both test-owned services were stopped. Synthetic journals and screenshots remain
  under `data-local/health-timeline-0094` and `data-local/health-disabled-0094`.

## Remaining requirements

The FINAL pack's Incident page is still incomplete: acknowledgement and resolution
custody, owner/runbook/recovery workflows, correlated trace/log/metric views,
realtime reconciliation, alert delivery and independent acceptance remain open.
The health/status and timeline are separate observations, not one global snapshot.
Journal pagination detects change rather than retaining historical page snapshots.
No incident can be closed or quarantine removed through this UI. P00–P13 remains
incomplete; scoped checks do not prove full-suite or release acceptance.
