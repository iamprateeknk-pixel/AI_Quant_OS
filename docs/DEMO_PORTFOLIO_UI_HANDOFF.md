# Demo portfolio observation display

## Delivered in increment 0129

The Portfolio screen keeps its authoritative paper instrument/currency panels and adds a read-only Exness MT5 demo account snapshot. It displays reported balance, credit, equity, profit, margin, free margin, account mode/leverage, saved positions and pending orders. No aggregation, cash-flow reconstruction, FX conversion, order control or capital authority is introduced.

The server reads only `/api/v1/portfolio/demo-observation`, using the existing HttpOnly operator session. Missing or rejected sessions produce a sign-in state before financial data is displayed. The existing local-origin allowlist, three-second request timeout, refusal of redirects, no-store fetch and 128 KiB streaming response bound apply. Session credentials are never passed into the panel. The backend's independent owner guard remains authoritative.

## Snapshot and expiry semantics

- Always label values as recorded/saved observations. `AVAILABLE` means the original local source window had not expired when the page was rendered, not native connectivity or account freshness.
- Validate original UTC timestamps at microsecond precision. The original source lease must be positive and at most three seconds. Republishing cannot extend it.
- Expired observations keep their saved amounts and original timestamps with an explicit current-state-unknown warning.
- Expiry is checked at page fetch time; this server-rendered panel does not update itself. A native document reload link fetches the protected snapshot again rather than relying on the client route cache.
- Unconfigured, missing, empty, withheld, invalid and sign-in states do not invent financial zeroes or positions. A saved empty positions/orders list describes only that observation.
- All observation/publication/record authority flags must be false; native freshness, account freshness and history attestation remain unverified, and reads remain sequential.

## Validation and privacy

Amounts remain the original decimal strings, including signs and trailing digits. No display rounding or financial arithmetic occurs. Decimal validation rejects nonfinite, excessively long and floating-point overflow/underflow strings; unsupported representations withhold the whole display instead of being converted to zero. Native numeric tickets/identifiers/leverage must be positive safe JavaScript integers; larger backend identities are withheld to avoid showing an altered identity. Symbols, enumerations, currency consistency, duplicate identities and eight-row limits are checked. Only selected fields reach the rendered view; extra credential/private fields are discarded.

The frontend fixture was exported from an actual protected API response using trusted simulated native data. The backend contract check compares all fields except the per-capture UUID and receipt commitment, which necessarily vary between runs. Parser tests cover authority, exact expiry boundaries, decimal and identity failures; server-reader tests cover missing/rejected sessions, fixed path and response bounds; rendered markup checks cover recorded values, expiry, sign-in and empty states.

## Outstanding acceptance

Browser layout, keyboard/focus, overflow and responsiveness remain pending: the earlier browser launch was blocked by policy, and no alternate launch was attempted. Static React rendering, frontend tests and a production build do not certify browser behavior. Native SDK/account completeness, terminal custody, correction/cancellation/financing accounting, installed services, capacity rollover, independent critical review, all P13 gates and full-system acceptance remain open. No SDK, terminal, real credentials or live capital was exercised.
