# ADR 0199: Improve Mission Control contrast and verify route states

- Status: accepted for the signed-out PAPER route shell
- Date: 2026-10-13

## Context

The shared muted text token was below the target contrast for some surfaces, and
the empty Market Detail chart used a gradient that prevented automated contrast
analysis from determining its background.

## Decision

Use a lighter shared muted text token and place empty chart copy on an opaque
panel surface with a visible border. Run axe-core against the WCAG 2.1 A/AA tags
on each Mission Control navigation route after the frontend is ready.

## Verification

The signed-out PAPER browser scan returned HTTP 200 for all 16 routes and zero
axe violations or incomplete checks. A keyboard Tab walk reached 386 controls across all 16 routes at 1280x900;
  pixel comparisons found no focused/unfocused crop below 2% changed pixels.
The scan record and limitations are in `docs/MISSION_CONTROL_ACCESSIBILITY_HANDOFF.md`.

## Consequences

The tested route shells and their initial signed-out states have automated
accessibility evidence. This does not establish authenticated workflow coverage,
screen-reader usability, the required six visual-state fixtures, or independent
P00-P13 acceptance.

## Rollback

Revert the muted token and empty-chart surface together with this record and the
accessibility handoff. No stored data or API contract changes are involved.
