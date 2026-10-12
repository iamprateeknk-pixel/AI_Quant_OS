# Architecture boundary handoff

## Summary

The deterministic domain slice named in `.clean/architecture.md` now has a
machine-readable import rule in `.clean/architecture.json`. The existing
architecture test reads that rule and checks every declared domain source for
forbidden framework, storage, HTTP composition, broker, and execution imports. It
also verifies that literal `importlib.import_module`, aliased `import_module`, and
`__import__` calls are included in the check.

## Verification

- `tests/test_architecture_broker_boundary.py`: 4 passed in a fresh archive of
  `HEAD` with only the changed test and declaration overlaid.
- The rule names exactly the five documented modules: `contracts.py`, `market.py`,
  `portfolio.py`, `risk.py`, and `backtest.py`.

## Limits and next work

The package inventory is explicit, but the other modules are not yet assigned to
architecture layers or checked against per-layer dependency rules. The detector also
does not detect names assembled at runtime.
Package-wide dependency direction, other phase gates, independent review, and all
P00–P13 acceptance remain open. This change does not authorize a broker or live
trading.

## Rollback

Revert the declaration, test changes, and this handoff together. No data or runtime
state is changed.
