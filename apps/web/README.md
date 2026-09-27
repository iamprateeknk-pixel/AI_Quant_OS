# Mission Control web

This Next.js app provides the local paper-mode operator view for AI Quant OS. Start the Python control API at `127.0.0.1:8000`, run `npm ci` and `npm run dev` here, then open `127.0.0.1:3000`.

`AI_QUANT_API_URL` may point the server to another trusted control API. It is never exposed through a `NEXT_PUBLIC_` variable. The browser has no direct broker access or control token.

The navigation follows the final pack's routes and adds a paper portfolio view. System status, paper orders and fills, EURUSD paper portfolio, risk state, broker simulator health, simulated quotes, and local audit read from the backend. Other routes display explicit unavailable states until their services are implemented.
