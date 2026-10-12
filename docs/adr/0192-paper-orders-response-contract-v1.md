# ADR 0192: Version the paper orders response contract

- Status: accepted for read-only API documentation
- Date: 2026-10-13

## Context

`GET /api/v1/orders` feeds the Orders view and Mission Control's lifecycle references. The API projection has explicit PAPER routing, a bounded set of durable lifecycle states, and an optional broker order reference, but no published schema fixed that contract.

## Decision

Publish a strict JSON Schema 2020-12 contract for the `{"orders": [...]}` response and validate the actual `_order_view` projection. Require the fixed PAPER route and four instruments, explicitly enumerate pending, dispatching, uncertain, accepted, rejected, partially filled, and filled states, and allow the broker reference only as a nonempty string when present.

## Consequences

This documents the existing response without changing endpoint output or order behavior. The schema does not establish that an uncertain order is resolved or that a broker reference is externally verified. P00/P05/P12 acceptance remains open.
