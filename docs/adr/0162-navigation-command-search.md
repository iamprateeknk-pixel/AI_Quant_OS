# ADR 0162: Add keyboard driven destination search

Date: 2026-10-12

## Decision

The application shell provides a searchable list of its registered destinations. The search opens from the header or Ctrl+K/Command+K, supports filtering by label, group, or route, and only links to existing navigation destinations. A native modal dialog supplies focus containment and Escape dismissal; on narrow screens the trigger keeps a compact accessible label.

## Limits

The palette does not search private records or invoke actions. Browser interaction and responsive visual review remain pending; static tests and the production build do not count as visual acceptance. The remaining P00-P13 gates are still open.
