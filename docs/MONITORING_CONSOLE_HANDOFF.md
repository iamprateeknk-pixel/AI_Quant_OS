# Incident console monitoring observations — increment0156

The Incidents page reads the protected local monitoring endpoint on the Next.js
server through the existing HTTP-only operator session. It reads the independent
health status, retained health journal and monitoring source concurrently. No
browser-to-Prometheus request or source/credential override is introduced.
Existing health incidents/quarantine records remain distinct from these local
rule observations, and no acknowledgement/control/automatic recovery is added.

The Paper metrics alerts panel shows both original selected rule states and UTC
evaluation/active times. Pending and firing are distinct; inactive does not imply
zero incidents, healthy orders or recovered risk. The native API fixture retains
actual original response bytes/timestamps from0155 without normalization. Its
hash and Python model/JSON contract agree; the frontend decodes that same fixture.

The decoder selects public contract fields only, validates both fixed rule names,
paths, states, holds and authority flags, and rejects incoherent/foreign/duplicate/
stale/future data. Original microseconds use integer arithmetic for45-second
freshness and120-second firing holds. Unknown private payloads never become
client fields. Disabled/unavailable/sign-in and stale-at-render cases infer no
rule rows. These are point-in-time snapshots; the panel explicitly says it does
not update automatically. Refresh obtains a new snapshot through the backend.

Fixed runbook links reach `/incidents/runbook#scrape-failed` and `#scrape-missing`,
with served human-readable guidance and links back to Incidents/Risk Center.
The original checked-out operations runbook remains the deployment reference.
No private source/config output, arbitrary URL, raw HTML, untrusted annotation
content or file path becomes a link. Operational dynamic routes gain general
loading/error boundaries; the installed Next16.3.6 retry() contract is used
without displaying/logging raw errors or retrying a command.

Semantic sections/headings, UTC time elements, captioned table/scope headers,
status messages and existing44px focusable links/buttons are used. Existing
application theme/spacing and scroll container are reused. No stylesheet or
shared visual redesign was introduced. Static React markup tests are not browser
visual, keyboard, assistive-technology or viewport acceptance.

Evidence0156 records fresh staged backend fixture/native regression tests,
full frontend tests, scoped ESLint and production Next/TypeScript build. The
backend package/dependencies and all112 schemas are unchanged from0155.
Browser launch was not retried after the earlier policy refusal. All full
P00–P13 gates, realtime monitoring integration, durable incident acknowledgement,
external notification/engine-health delivery and production/independent/browser
acceptance remain incomplete.
