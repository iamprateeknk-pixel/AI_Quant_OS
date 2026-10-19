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

- other authenticated workflows, responsive keyboard paths beyond the Risk Center and destination dialog, and manual screen-reader review;
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

The local screenshot folder is ignored by Git. Preserve it locally when reviewing the states; it is not part of a commit.
