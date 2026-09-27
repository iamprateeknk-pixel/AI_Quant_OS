# ADR 0001 Paper first deterministic boundary

Status: Accepted for this initial implementation.

The final blueprint assigns capital authority to a deterministic risk kernel and execution gateway. Agents and models may propose intents; they may not submit broker orders or edit risk ceilings. This repository starts with only a simulated paper route. The API accepts an intent, evaluates the current quote and fixed risk limits, and then submits the approved decision to the paper gateway. There is no broker SDK dependency or live enablement setting.

This keeps early tests free of capital exposure while preserving the intent, risk decision and order-command boundary. Local SQLite retains paper receipts and audit events when configured. It does not establish broker eligibility, tamper-proof audit, portfolio truth, or permission to trade live. Those require later phase acceptance and a separate ADR.
