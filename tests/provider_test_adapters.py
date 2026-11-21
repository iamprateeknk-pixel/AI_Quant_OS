"""Fixture factories for external provider adapters with real spend journals."""

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone

import httpx

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.jev_adapter import JevClassifier
from ai_quant_os.jev_spend_policy import JevInputSpendPolicy
from ai_quant_os.openai_reviewer import OpenAIReviewer
from ai_quant_os.provider_call_admission import ProviderOwner
from ai_quant_os.provider_spend_policy import ProviderSpendPolicy, SpendRule
from ai_quant_os.provider_spend_store import ProviderSpendStore

FIXTURE_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FixtureInputCounter(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(
                200,
                json={"object": "response.input_tokens", "input_tokens": 100},
                request=request,
            )
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _rules() -> tuple[SpendRule, ...]:
    return tuple(
        SpendRule(role, 1_000_000_000, 1_000_000_000, 0, 0, 10**15, 10**15, 0, 0)
        for role in ROLE_CEILINGS
    )


def jev_with_fixture_spending(**options) -> JevClassifier:
    temporary = TemporaryDirectory(prefix="jev-test-spend-")
    policy = JevInputSpendPolicy(
        model=options["model"],
        input_nano_usd=42,
        output_nano_usd=0,
        rate_reference="https://docs.typesafe.ai/models observed 2026-10-10",
        rules=_rules(),
    )
    options["spend_store"] = ProviderSpendStore(Path(temporary.name) / "spend.sqlite3", policy)
    options["spend_owner"] = ProviderOwner("A02", "0.2.0")
    options.setdefault("utc_clock", lambda: FIXTURE_NOW)
    adapter = JevClassifier(**options)
    adapter._test_spend_directory = temporary
    return adapter


def reviewer_with_fixture_spending(**options) -> OpenAIReviewer:
    temporary = TemporaryDirectory(prefix="openai-test-spend-")
    policy = ProviderSpendPolicy(
        model=options["model"],
        input_nano_usd=2,
        output_nano_usd=8,
        rate_reference="test-only declared rate ceiling",
        rules=_rules(),
    )
    options["spend_store"] = ProviderSpendStore(Path(temporary.name) / "spend.sqlite3", policy)
    options["spend_owner"] = ProviderOwner("A03", "0.2.0")
    options.setdefault("utc_clock", lambda: FIXTURE_NOW)
    if options.get("transport") is not None:
        options["transport"] = _FixtureInputCounter(options["transport"])
    reviewer = OpenAIReviewer(**options)
    reviewer._test_spend_directory = temporary
    return reviewer
