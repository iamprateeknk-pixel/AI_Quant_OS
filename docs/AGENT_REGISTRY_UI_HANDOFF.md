# Agent registry UI handoff

Date: 2026-10-09

## Implemented

`/agents` reads the existing public paper registry on the server without caching.
It displays every registered version, role, squad, development status and lifecycle.
Expandable contracts show the mission, output, required inputs, declared tools,
timeout and immutable manifest reference. The current local catalog contains 18
roles and 29 versions, including planned original versions and partial successors.

The boundary validates the paper/schema markers, role identifiers, version syntax,
bounded manifest strings and lists, lifecycle/development enums, finite timeout,
manifest reference syntax and unique role/version identities. It projects only
declared public fields. Invalid responses withhold the whole registry. An empty
registry is distinct from an unavailable service. Counts come from the response.

Development status is not evaluation coverage or production acceptance. Lifecycle
does not assert agent health. Declared tools are permission ceilings, not evidence
that executable handlers exist. The page performs no agent action or capital action.

## Verification

- Three new boundary checks were added before implementation; the missing parser
  failed two behavior checks. All 15 frontend unit checks subsequently passed.
- The final Next.js production build/type checks and frontend lint passed.
- Installed Edge checked the production page against the actual paper API: all
  29 versions and their statuses matched, details opened by keyboard, and the
  390px page had no document overflow. Browser page errors were empty.
- Stopping the test-owned API produced an unavailable registry with no displayed
  rows. Both test-owned services were stopped after verification.
- The existing backend registry/permission suite passed 100 checks. This is
  scoped regression evidence, not a new full backend suite or independent review.

## Remaining requirements

This advances the FINAL frontend pack's Agent Control page; it does not complete
it. Health, current tasks, genome/model routes, latency, cost, calibration, last
errors, evidence/tool/memory/evaluation histories, controlled diagnostics,
quarantine/restart requests and challenger comparisons remain unwired. Saved graph
execution summaries link to the existing trace view. No action controls are offered
without their backend admission and audited outcome workflow.
Automatic refresh/realtime reconciliation and independent frontend acceptance also
remain open. The entire P00–P13 execution pack remains incomplete.

## Update: per-version execution history

The contract details now link to the archived execution list filtered by the exact
agent ID and version. The protected graph-run endpoint accepts the filter only as a
complete validated pair, keeps the archive head bound across pages, and returns
metadata summaries only. Each page is checked so every row matches the selected
version before it is rendered. This is historical advisory evidence; it does not
show current eligibility or provide execution controls. See ADR 0226.
