# Mission Control web

This Next.js app provides the local paper-mode operator view for AI Quant OS. Start the Python control API at `127.0.0.1:8000`, run `npm ci` and `npm run dev` here, then open `127.0.0.1:3000`.

`AI_QUANT_API_URL` selects the server's control API. Authenticated operator forwarding requires a literal local `http://127.0.0.1:<port>` origin. `AI_QUANT_WEB_ORIGIN` sets the exact dashboard origin (default `http://127.0.0.1:3000`). Neither setting uses a `NEXT_PUBLIC_` credential variable.

Risk Center now supports backend-owned sign-in, sign-out, re-authenticated paper safety actions and durable accepted receipts. Sessions expire after fifteen minutes. The backend nonce is held in an HttpOnly SameSite Strict cookie. See [operator handoff](../../docs/OPERATOR_CONTROL_HANDOFF.md) for custody, retry behavior and remaining P12 work.

The navigation follows the final pack's routes and adds a paper portfolio view. System status, paper orders and fills, EURUSD paper portfolio, risk state, broker simulator health, simulated quotes, and local audit read from the backend. Other routes display explicit unavailable states until their services are implemented.
