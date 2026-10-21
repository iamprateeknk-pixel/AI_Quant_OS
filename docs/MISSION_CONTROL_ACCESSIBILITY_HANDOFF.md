# Mission Control accessibility handoff

## Scope and verification

- Axe-core WCAG 2.1 A/AA tags were run in a real browser against the signed-out
  PAPER state for `/`, `/world-state`, `/markets/EURUSD`, `/brokers`,
  `/strategies`, `/agents`, `/risk`, `/portfolio`, `/orders`, `/research`,
  `/evolution`, `/incidents`, `/traces`, `/costs`, `/audit`, and `/settings`.
- All 16 routes returned HTTP 200. The scan found zero violations and zero
  incomplete checks, including on the empty Market Detail chart state.
- A real-keyboard Tab walk reached 386 focusable controls across all 16 routes at
  1280x900. Each had a visible focus state; pixel comparisons found no
  focused/unfocused crop pair below 2% changed pixels (minimum 4.41%). The full
  crops and comparison data are in
  `apps/web/output/playwright/focus-20261012T2054Z/` (locally ignored).
- The muted text token was raised and the empty chart copy received an opaque
  panel surface so contrast can be evaluated against a known background.

## Authenticated responsive route and table checks (2026-10-20)

- The production build was checked with a synthetic operator session on all 16
  navigation routes, the incident runbook, and an unknown-trace detail at
  1280x800, 390x844, and 320x800. All 54 route/viewport combinations had zero
  axe WCAG 2.1 and 2.2 A/AA violations or incomplete checks, including zero
  WCAG 2.2 `target-size` findings; route mismatches, horizontal page overflow,
  and console errors were also zero. Evidence:
  `docs/evaluations/mission-control-authenticated-accessibility-2026-10-20.json`.
- At 320 px, a real sequential Tab walk reached all eight scrollable table
  regions on Dashboard, Brokers, World State, and Portfolio. At 390 px, all eight
  regions scrolled horizontally with ArrowRight and back with ArrowLeft after
  focus. The browser scan used an empty PAPER order/fill database.

### Authenticated keyboard focus walk (2026-10-20)

- The production build returned HTTP 200 on all 18 routes with a fresh synthetic
  operator session at 320x800, 390x800, and 1280x800. Real Tab traversals
  observed 140, 143, and 414 stops respectively, returning to the first stop on
  every route at every width. Focus indicators were visible on all measured
  controls; the smallest changed-pixel ratios in the 12 px outline band were
  4.28%, 9.04%, and 16.10%. There were no page or console errors. Ordered
  trails and session details are in the locally ignored files
  `apps/web/output/playwright/p12-auth-focus-20261020/focus-trail.json`,
  `apps/web/output/playwright/p12-auth-focus-390-20261020/focus-trail.json`, and
  `apps/web/output/playwright/p12-auth-focus-1280-20261020/focus-trail.json`.
- The destination dialog's Tab containment and Escape restoration were covered
  by the authenticated 2026-10-19 keyboard run. A separate authenticated pass
  tested actual clipboard paste and simulated composition in seven reachable
  text fields on Brokers, Risk Center, and Traces; all seven passed. See
  `apps/web/output/playwright/p12-auth-focus-20261020/text-input-probe.json`
  (locally ignored). A native operating-system IME and manual screen-reader use
  were not tested. No form, safety action, or order was submitted; the PAPER
  database had no orders or fills.

## Limits and remaining work

The original axe scan and pixel-focus captures cover the initial signed-out route
state; the authenticated focus walk is recorded above. Separate authenticated
safety-action and visual-state browser evidence
is recorded in `BROWSER_ACCEPTANCE_HANDOFF.md`. The native destination-search dialog keeps
focus contained through Tab and restores focus on Escape; Ctrl+K also works at
320 px without horizontal overflow. The authenticated Risk Center keyboard path
was also checked at 320 px, including sign-in, control-field order, skipped
disabled action, and sign-out. Manual screen-reader and target-spacing review,
authenticated action submission paths outside the existing Risk Center run, and
populated orders/fills remain open, along with the exact
broker `DEGRADED` state because the current backend contract only exposes
`CONNECTED`, `DISCONNECTED`, and `UNKNOWN`. Applicable P00-P13 release gates also
remain open.

## Rollback

Revert ADR 0199, the contrast token and empty-chart styling, and this handoff.
No persistent state or backend contract is changed.
