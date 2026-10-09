"""Keep the frontend fixture aligned with the actual private observation API."""

import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.mt5_portfolio_observation import build_portfolio_publication
from ai_quant_os.mt5_sync_service import publish_portfolio
from test_mt5_account_sync import NOW
from test_mt5_sync_controller import setup


def test_demo_portfolio_ui_fixture_matches_actual_protected_api(tmp_path):
    fixture = json.loads(
        Path("apps/web/tests/fixtures/demo-portfolio.json").read_text(encoding="utf-8")
    )
    _, controller, gateway, projection, *_ = setup(tmp_path)
    try:
        controller.tick()
        publish_portfolio(
            tmp_path / "portfolio.json",
            build_portfolio_publication(
                controller, UUID(fixture["observation"]["publication"]["run_id"]), clock=lambda: NOW
            ),
        )
        with TestClient(
            create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
        ) as client:
            response = client.get(
                "/api/v1/portfolio/demo-observation", headers={"X-Operator-Token": "operator"}
            )
            assert response.status_code == 200
            actual = response.json()
        # Each capture has a new UUID and corresponding receipt commitment.
        for field in ("source_attempt_id", "source_receipt_ref"):
            actual["observation"]["publication"]["record"][field] = fixture["observation"][
                "publication"
            ]["record"][field]
        assert actual == fixture
    finally:
        controller.close()
        projection.close()
        gateway.close()
