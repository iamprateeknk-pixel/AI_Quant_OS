# Browser acceptance evidence

Date: 2026-10-13

Environment: isolated local PAPER API and production Next.js build; no broker connection or live execution.

## Visual state captures

The six current captures are in the locally ignored `apps/web/output/playwright/visual-states/` folder:

| File | Browser state | Evidence source |
| --- | --- | --- |
| `normal-paper-dashboard.png` | Initial PAPER dashboard | Local application |
| `no-new-risk-accepted.png` | Safety action admitted and durable receipt shown | Authenticated browser action and authoritative control read |
| `paper-mode-no-new-risk-dashboard.png` | PAPER dashboard with `NO_NEW_RISK` active | Same isolated PAPER state |
| `broker-disconnected.png` | PAPER broker reports `Disconnected`; measurements remain unmeasured | Isolated API started with the simulated broker disconnected in a test-only entrypoint |
| `agent-quarantined.png` | `A02@0.1.0` quarantined | Health watchdog consumed a retained failed graph receipt |
| `incident-active.png` | `AGENT_FAILED` incident and `NO_NEW_RISK` visible | Same watchdog-generated retained failure state |

The broker contract only exposes `CONNECTED`, `DISCONNECTED`, and `UNKNOWN`; it has no `DEGRADED` state. The disconnected capture is the closest implemented broker failure state and is labeled accurately in the UI. Do not represent it as a measured degradation score.

## Browser checks

- All 16 navigation routes returned HTTP 200 at 1280 px and 320 px, with no horizontal overflow or browser console errors during that route-shell scan.
- Axe-core WCAG 2.1 A/AA found zero violations and zero incomplete checks across all 16 signed-out PAPER route shells.
- A real keyboard Tab walk reached 386 controls at 1280x900. Focused/unfocused screenshot comparisons found no focus indicator below the 2% pixel-change threshold; minimum change was 4.41%.
- The native destination search dialog opened with focus on its query input. Five Tab presses stayed within the modal; Escape closed it and restored focus to the trigger. At 320 px, Ctrl+K opened it with focus contained and no horizontal overflow. The browser reported no console errors during this dialog check.
- The authenticated `NO_NEW_RISK` flow showed a durable receipt, refreshed authoritative state, and removed the sign-in and reauthentication fields after success. The API returned `ACCEPTED`, and the receipt read returned the same action ID with `NO_NEW_RISK`.
- The monitoring journal 422 was reproduced. The browser proxy supplied `limit=50`, but the backend declares a fixed `Literal[50]` query parameter and rejected the URL string. The proxy now omits that redundant parameter and relies on the backend's fixed default. The regression test failed before the change and passed after it; the authenticated production-build browser request returned HTTP 200 with a valid empty PAPER journal.
- The disconnected-broker browser capture displayed the backend's `DISCONNECTED` state and unmeasured fields. Its final browser session had zero console errors.
- In that capture, the configured-route column is labeled `Adapter enabled`. This reports the adapter's configuration flag separately from connection health and backend order-admission policy.

## Remaining acceptance work

These checks do not establish release readiness. The following remain open:

- keyboard paths for authenticated interactive workflows outside the Risk Center and destination dialog, manual target-spacing review and screen-reader review;
- the exact broker `DEGRADED` design state, if that state is added to the backend contract;
- independent security and risk review, production identity/secret/compliance decisions, and all applicable P00-P13 release gates;
- shadow and live acceptance; live execution remains unreleased.

The local authenticated Risk Center keyboard path and destination dialog were
rechecked against the production-built UI at 320px. Focus stayed inside the dialog,
Escape restored the trigger, Ctrl+K reopened it without horizontal overflow, and
the authenticated control fields followed keyboard order through the instrument
selector. The synthetic session was signed out, the protected session read returned
401, and no safety action was submitted. See
`docs/evaluations/mission-control-authenticated-keyboard-2026-10-19.json`.

On 2026-10-20, an isolated production UI/API run established a synthetic operator
session and visited the authenticated Agents, Brokers, Orders, Audit, Costs,
Settings, Traces, and Incidents routes. Every route stayed at its requested URL;
the current-session inventory and backend-backed page states rendered, with zero
browser console errors or warnings and all listed UI/API requests returning HTTP
200. The run used a fresh empty PAPER database and submitted no safety action or
order. The viewport was not pinned; overflow, axe, target-size, screen-reader, and
responsive keyboard checks were not part of this smoke run. Exact observations and
limits are in `docs/evaluations/mission-control-authenticated-route-smoke-2026-10-20.json`.

The local screenshot folder is ignored by Git. Preserve it locally when reviewing the states; it is not part of a commit.

On 2026-10-20, all 16 Mission Control navigation routes, the incident runbook,
and an unknown-trace detail were scanned against axe-core WCAG 2.1 and 2.2 A/AA
tags at 1280x800, 390x844, and 320x800 on the production build. All 54
route/viewport combinations had zero axe violations, including the WCAG 2.2
`target-size` rule, zero incomplete checks, zero horizontal page overflow, zero
route mismatches, and zero console errors. Eight overflowing
table regions on Dashboard, Brokers, World State, and Portfolio were explicitly
focused at 390 px and moved right 96 px with ArrowRight, then back to the left
edge with ArrowLeft. A separate real Tab walk at 320 px reached all eight table regions. See
`docs/evaluations/mission-control-authenticated-accessibility-2026-10-20.json`.
This automated scan does not replace manual screen-reader and target-spacing review.
The PAPER database had no orders or fills, so populated order/fill and saved-demo
portfolio rows were not covered.

## Local visual comparison

`apps/web/scripts/compare-visual.mjs` compares two PNG captures at the same dimensions, reports the changed-pixel ratio, and writes a magenta diff image. It accepts an optional per-channel pixel threshold and explicit masks for dynamic rectangles. Example:

```powershell
npm run visual:compare -- --reference output/playwright/visual-states/normal-paper-dashboard.png --current output/playwright/visual-states/normal-paper-dashboard.png --diff output/playwright/visual-states/normal-paper-dashboard.diff.png --max-diff 0.01 --pixel-threshold 8
```

`apps/web/scripts/compare-visual-suite.mjs` checks the six required states together: normal PAPER dashboard, `NO_NEW_RISK`, broker disconnected (the implemented broker failure state), agent quarantine, active incident, and the shadow agent registry. It fails on a missing reference/capture or any over-budget state and writes one diff image per comparable state. Pass `--masks-json` with a state-name-to-rectangle map when a known dynamic area needs masking. Example:

```powershell
npm run visual:compare-suite -- --reference-dir output/playwright/visual-states --current-dir output/playwright/visual-captures --diff-dir output/playwright/visual-diffs --max-diff 0.01 --pixel-threshold 8
```

The retained-state self-comparison only verifies that the six local references are present and the comparison runner can read them; it is not a fresh-build regression result. The screenshot command and state-seeding procedure remain manual. Stable cross-machine regression still needs reproducible fixture setup, fixed time/session values, and a pinned browser/viewport.

The retained six screenshots are local inspection captures, not deterministic CI baselines. They contain session-specific IDs and timestamps and were produced with varying viewport heights. Compare a fresh capture against a matching local reference at the same viewport; mask only known dynamic regions. CI-quality pixel regression still needs reproducible seed data, fixed time/session values, and a browser/viewport pin. The illustrative execution-pack dashboard is not a valid baseline because its trading values are fabricated.

On 2026-10-19, an additional production-build capture was taken at
`apps/web/output/playwright/visual-states/agent-shadow-registry.png`. The Agents
view showed 18 roles and 42 registered versions, each carrying the backend-owned
`SHADOW` lifecycle under the PAPER environment, with no capital-authority claim.
This adds a retained shadow-state fixture; it is a visual snapshot, not an
automated pixel-diff regression result. See
`docs/evaluations/mission-control-agent-shadow-visual-2026-10-19.json`.
