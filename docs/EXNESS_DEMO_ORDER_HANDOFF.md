# Exness MT5 demo order gateway handoff

This is a local, single-operator development gateway for an explicitly enrolled
Exness demo account. It submits only market orders through the separately
supervised MT5 worker. The existing PAPER router remains PAPER-only, and this
gateway does not provide live account support or continuous background reconciliation.

## Enrollment and command files

Create an enrollment JSON file with exactly these top-level fields:

```json
{
  "connection": {
    "enabled": true,
    "connection_ack": "CONNECT_DECLARED_DEMO_ONLY",
    "enrollment_id": "replace-with-a-UUID",
    "terminal_path": "C:/Program Files/MetaTrader 5/terminal64.exe",
    "sdk_version": "5.0.1",
    "expected_login": 610001,
    "expected_server": "your-demo-server",
    "expected_company": "Exness ...",
    "expected_currency": "USD",
    "symbols": [{"instrument_id": "EURUSD", "venue_symbol": "EURUSDm"}]
  },
  "enabled": true,
  "submission_ack": "SUBMIT_EXNESS_DEMO_ONLY",
  "max_order_lots": "0.10"
}
```

Replace the example account and symbol details with the values confirmed in the
local MT5 demo terminal. The file is bounded to 16 KiB and rejects unknown or
duplicate fields. Never put a password in the file. Set
`AI_QUANT_MT5_PASSWORD` in the invoking process environment; the worker receives
it through bounded standard input and filters `AI_QUANT_MT5_*` variables from
its own inherited environment.

The command file must be one exact `OrderCommand` JSON object with no extra
fields. It must name `EXNESS_MT5_DEMO`, a configured instrument, a market order,
and IOC or FOK time in force. Quantity is expressed in instrument units and
converted to MT5 lots using the current symbol contract size. Submission is
withheld when the symbol's volume minimum, maximum, step, or enrollment cap is
violated. The account is checked as DEMO and bound to the enrollment's expected
login, server, company, and currency before submission.

## Explicit invocation

Use a local absolute state directory so the durable idempotency database remains
on the operator workstation:

```powershell
$env:AI_QUANT_MT5_PASSWORD = '<demo-terminal-password>'
ai-quant-demo-order `
  --enrollment C:\path\to\enrollment.json `
  --command C:\path\to\one-order.json `
  --state-dir C:\AI_Quant_OS_State\exness-demo `
  --submit-demo-only
```

The final switch is mandatory. The SQLite attempt store reserves each
idempotency key before the native send and binds it to both the exact order and
account scope. If worker custody or the broker reply is uncertain, the result
is `UNKNOWN` or `WITHHELD`; the CLI never retries automatically. The operator
must reconcile the attempt before creating any new command. Reusing the same key
with a changed order or account is rejected. To inspect one uncertain attempt,
use the original enrollment, command, and state directory with the explicit
read-only mode:

```powershell
ai-quant-demo-order `
  --enrollment C:\path\to\enrollment.json `
  --command C:\path\to\one-order.json `
  --state-dir C:\AI_Quant_OS_State\exness-demo `
  --reconcile-demo-only
```

Reconciliation verifies the enrolled DEMO account and searches at most seven
days of order history for one match on the expected symbol, side, volume,
timestamp, and deterministic magic/comment tag. It never calls `order_send`.
Missing, ambiguous, malformed, or mismatching history remains `UNKNOWN`; a
unique exact match is saved durably and returned on later reconciliation.

## Acceptance still required

This CLI has only been exercised with injected test runners and fake MT5
responses. No terminal account was connected and no order was submitted for
this increment. Before treating P04 as accepted, complete supervised native
tests on the enrolled Exness demo terminal, validate native history fields and
post-timeout reconciliation, exercise service restart and disconnect recovery, and obtain
independent P04/security review. Live execution remains disabled pending the
P13 release gates.

Current host check: the Python `MetaTrader5` package is not installed, and
`terminal64.exe` is absent from both standard MetaTrader 5 installation paths
checked on this workstation. Native acceptance cannot run in the current
environment until the terminal and package are installed and an operator-owned
demo enrollment is configured. No credential or terminal account data was read.
