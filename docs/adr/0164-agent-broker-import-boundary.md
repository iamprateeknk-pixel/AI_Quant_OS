# ADR 0164: Guard the agent and strategy broker import boundary

Date: 2026-10-12

## Decision

A focused architecture test parses every `*_agent.py` module and the registered built-in strategy sources. It rejects imports of broker/router/execution modules and the MT5 SDK, including package-level and common dynamic import forms. This keeps direct broker access out of agent and strategy implementation code while preserving the existing service-owned routing boundary.

## Limits

The static check detects imports in the named source files; it does not prove the complete indirect runtime call graph, constrain generated code, or replace independent architecture review. The remaining P00 acceptance items and all other P00-P13 gates remain open.
