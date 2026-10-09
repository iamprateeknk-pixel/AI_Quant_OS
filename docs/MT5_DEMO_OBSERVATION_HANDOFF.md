# MT5 demo observation boundary — increment 0108

## Implemented

`ai_quant_os.mt5_demo_observation.capture_demo` accepts a validated enrollment and
an injected SDK facade. It imports no MetaTrader5 package and is deliberately
absent from API composition and the broker router. Disabled requests make no
native calls. This boundary is intended for a future supervised observation
worker, not direct use from the trading process.

Enabled enrollment declares an exact SDK version, local absolute terminal EXE,
explicit credentials and login, exact company/server/currency, and one-to-one
canonical-to-venue symbol mapping. Only EURUSD, GBPUSD, USDJPY and EURJPY are
supported. Venue suffixes are never guessed. Additional fields are rejected.

Connection uses the declared account with a 3000 ms initialize timeout. Before
and after quote collection, terminal connectivity and all account identifiers
must match, with trade mode exactly integer zero (demo). Contest, real accounts,
boolean aliases and changed accounts withhold the observation. This is a local
SDK response check, not independently attested broker identity or eligibility.

Quotes retain actual bid/ask, millisecond UTC tick time, contract size, digits
and volume bounds. Missing, malformed, crossed, nonfinite, future or at least
three-second-old ticks are withheld. A capture taking over five seconds or
regressing its UTC clock is withheld. Decimal conversion preserves the native
value's string representation; it cannot recover precision already lost by the
SDK's floating point representation. Snapshot expiry is the earliest tick's
three-second freshness deadline. Consumers must recheck that deadline.

Shutdown is attempted after a connection attempt; cleanup failure discards a
successful capture. Public reports contain fixed failure codes and no login,
terminal path, server, password, account name, raw errors or SDK diagnostic text.
Every result explicitly denies order and portfolio authority and marks native
attestation UNVERIFIED. No order, login, symbol selection or trading configuration
mutation methods exist in the injected protocol.

## Remaining P04 work

This increment supplies the testable observation policy only. A separately
supervised native worker, bounded IPC, hard wall-clock cancellation and owned
process custody are still required before any SDK is imported or invoked in an
operational path. The initialize timeout does not bound later native calls.
Shutdown disconnects the Python client; it does not establish that a terminal
launched by initialize was terminated. There is no automatic retry here.

No native terminal, SDK package or credentials were used for verification.
Fixture success is not an Exness integration acceptance. Account, position,
order and fill synchronization; reconnect handling; durable order idempotency;
IBKR and Zerodha adapters; eligibility; independent review and P13 release gates
remain open. Existing paper routing and portfolio accounting are unchanged.

## Verification and rollback

Focused tests exercise account and symbol identity, account switching, disabled
capture, exact version, clocks, freshness, malformed facts, sanitized failures
and cleanup, together with existing broker routing checks. The retained evidence
records their fresh staged source hashes and output. Prior schemas remain intact.
There is no persistence migration or API route to roll back; remove this boundary
only while no future worker depends on it.

## Vendor reference

[MetaQuotes initialize documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py)
documents explicit connection parameters, timeout and potential terminal launch.
[Account information](https://www.mql5.com/en/docs/python_metatrader5/mt5accountinfo_py)
and [account properties](https://www.mql5.com/en/docs/constants/environment_state/accountinformation)
define the account response and demo/contest/real modes.
