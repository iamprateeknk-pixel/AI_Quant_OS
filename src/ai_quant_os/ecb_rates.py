"""One explicit, bounded public ECB request; no background or paid calls."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Callable

import httpx

from ai_quant_os.policy_rates import ECB_RATE_URL, MAX_RATE_CSV_BYTES, normalize_ecb_rates


@dataclass(frozen=True)
class RetrievedRates:
    raw: bytes
    received_at: datetime


class EcbRateClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if (
            type(timeout_seconds) not in {float, int}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 20
        ):
            raise ValueError("Invalid ECB total request timeout")
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock

    async def fetch(self) -> RetrievedRates:
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport, follow_redirects=False
                ) as client:
                    async with client.stream(
                        "GET",
                        ECB_RATE_URL,
                        headers={"Accept": "text/csv", "Accept-Encoding": "identity"},
                    ) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("content-type", "").split(";")[0].strip()
                            != "text/csv"
                            or response.headers.get("content-encoding", "identity") != "identity"
                        ):
                            raise ValueError("ECB retrieval response refused")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=4096):
                            if len(raw) + len(chunk) > MAX_RATE_CSV_BYTES:
                                raise ValueError("ECB retrieval exceeds byte budget")
                            raw.extend(chunk)
                        received_at = self._clock()
                        content = bytes(raw)
                        normalize_ecb_rates(
                            content, received_at=received_at, capture_mode="ECB_HTTPS_RETRIEVAL"
                        )
                        return RetrievedRates(content, received_at)
        except (TimeoutError, httpx.HTTPError) as error:
            raise ValueError("ECB retrieval unavailable") from error
