# ADR 0196: Strict browser safety and risk reads

- Status: accepted for read-only control presentation
- Date: 2026-10-13

## Context

The browser accepted arbitrary safety-state strings, negative order counts, loose risk values, and did not verify the instrument identity returned by the risk endpoint. These fields appear in operational control views.

## Decision

Project system status and risk reads through exact-field parsers. Allow only the backend's four safety states and configured instruments, require nonnegative integer order counts and valid risk decimal strings/bounds, and bind Risk Center's result to its requested EURUSD instrument.

## Consequences

Malformed or future-shaped responses are withheld rather than rendered as authoritative controls. The API and backend kernel remain the only risk/safety authority. Browser acceptance and P05/P12/full P00-P13 gates remain open.
