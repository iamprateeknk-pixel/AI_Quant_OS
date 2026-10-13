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

## Limits and remaining work

The axe and keyboard measurements cover the initial signed-out route state. The
separate authenticated safety-action and visual-state browser evidence is recorded
in `BROWSER_ACCEPTANCE_HANDOFF.md`. The native destination-search dialog keeps
focus contained through Tab and restores focus on Escape; Ctrl+K also works at
320 px without horizontal overflow. Other authenticated/modal interactions,
remaining responsive keyboard paths, and manual screen-reader review remain open,
along with the exact
broker `DEGRADED` state because the current backend contract only exposes
`CONNECTED`, `DISCONNECTED`, and `UNKNOWN`. Applicable P00-P13 release gates also
remain open.

## Rollback

Revert ADR 0199, the contrast token and empty-chart styling, and this handoff.
No persistent state or backend contract is changed.
