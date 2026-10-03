import asyncio
from datetime import datetime, timezone
import httpx
import pytest

from test_policy_rates import recipe

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def test_fixed_https_request_returns_validated_raw_bytes_and_real_receipt_time():
    from ai_quant_os.ecb_rates import EcbRateClient
    from ai_quant_os.policy_rates import ECB_RATE_URL

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=recipe(), headers={"content-type": "text/csv"})

    result = asyncio.run(
        EcbRateClient(transport=httpx.MockTransport(handler), clock=lambda: NOW).fetch()
    )
    assert result.raw == recipe() and result.received_at == NOW
    assert len(requests) == 1 and str(requests[0].url) == ECB_RATE_URL
    assert "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "fault",
    [
        "redirect",
        "status",
        "content_type",
        "oversized",
        "wrong_source",
        "missing_series",
        "timeout",
    ],
)
def test_network_or_invalid_data_failure_never_returns_a_retrieval(fault):
    from ai_quant_os.ecb_rates import EcbRateClient

    async def handler(request):
        if fault == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(
            302 if fault == "redirect" else 503 if fault == "status" else 200,
            headers={
                "content-type": "text/html" if fault == "content_type" else "text/csv",
                "location": "https://example.com/",
            },
            content=b"x" * 65537
            if fault == "oversized"
            else recipe().replace(b"PCPA", b"PC")
            if fault == "wrong_source"
            else b""
            if fault == "missing_series"
            else recipe(),
        )

    with pytest.raises(ValueError):
        asyncio.run(
            EcbRateClient(
                timeout_seconds=0.02, transport=httpx.MockTransport(handler), clock=lambda: NOW
            ).fetch()
        )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 21, True])
def test_invalid_total_request_timeout_is_refused(timeout):
    from ai_quant_os.ecb_rates import EcbRateClient

    with pytest.raises(ValueError):
        EcbRateClient(timeout_seconds=timeout)


def test_slow_stream_is_bounded_by_total_deadline():
    from ai_quant_os.ecb_rates import EcbRateClient

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(5):
                await asyncio.sleep(0.03)
                yield b"x"

    def handler(request):
        return httpx.Response(200, stream=SlowBody(), headers={"content-type": "text/csv"})

    with pytest.raises(ValueError, match="retrieval"):
        asyncio.run(
            EcbRateClient(timeout_seconds=0.05, transport=httpx.MockTransport(handler)).fetch()
        )
