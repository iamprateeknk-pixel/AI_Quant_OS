# 0156 — Display protected original monitoring observations in Incidents

## Decision

Reuse the existing server-only protected read boundary with fixed alerts path,
8192-byte response ceiling, no-store/no redirects and three-second deadline.
Compose it alongside independent health reads. Select the two current rule
observations and original UTC timestamps through a dedicated incident-feature
decoder; use integer microseconds at freshness/hold boundaries. Missing source
states infer no rows. Expose only fixed served runbook links and read refresh.
Use the installed Next page/cookies/loading/error contracts; generic operational
read fallback retries a read without issuing or repeating an operator command.

## Evidence and limits

Eight new frontend checks cover actual original native pending fixture, field
privacy, changed/duplicate/invalid authority/time/hold/path rejection, exact
microsecond boundaries, pending/firing/inactive/disabled/unavailable/sign-in/
stale display, protected bounded session fetch, runbook anchors and read fallback
markup. A backend fixture check binds exact original bytes to the native model.
Original first red tests failed on absent decoder/panel/reader. Final staged
full checks/build and source identity are recorded in evaluation0156.

Point-in-time/manual-refresh view only; real-time monitoring, durable custody/
acknowledgement, notifications and engine-health coverage remain pending. No
browser launch or rendered visual/accessibility acceptance; prior refusal and
unknown candidate/Docker custody remain. All full P00–P13 gates stay incomplete.

## Rollback

Revert additive incident feature/runbook/fallbacks and page/read wiring with
fixture/tests/docs. No backend/database/order-state/dependency/schema migration,
reset, retry, external message or service installation is required.
