"""Owner-protected read of one server-configured, unverified local heartbeat."""

import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, Response

from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_sync_service import ServiceHealthView, read_health
from ai_quant_os.mt5_portfolio_observation import DemoPortfolioView, read_portfolio


class DemoSyncHealthResponse(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    source_kind: Literal["LOCAL_UNVERIFIED_HEARTBEAT"] = "LOCAL_UNVERIFIED_HEARTBEAT"
    configured: bool
    observation: ServiceHealthView
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


class DemoPortfolioResponse(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    source_kind: Literal["LOCAL_UNVERIFIED_DEMO_PORTFOLIO"] = "LOCAL_UNVERIFIED_DEMO_PORTFOLIO"
    configured: bool
    observation: DemoPortfolioView
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False


def install_demo_sync_health_routes(app, require_operator, state_dir, clock):
    configured = state_dir if state_dir is not None else os.getenv("AI_QUANT_DEMO_SYNC_STATE")
    path = None
    if configured is not None:
        directory = Path(configured)
        if not directory.is_absolute():
            raise ValueError("Demo sync health requires an absolute server-owned state directory")
        path = directory / "health.json"

    @app.get(
        "/api/v1/brokers/demo-sync/health",
        response_model=DemoSyncHealthResponse,
        dependencies=[Depends(require_operator)],
    )
    def demo_sync_health(response: Response) -> DemoSyncHealthResponse:
        response.headers["Cache-Control"] = "no-store"
        observation = (
            read_health(path, clock=clock)
            if path is not None
            else ServiceHealthView(state="UNAVAILABLE")
        )
        return DemoSyncHealthResponse(configured=path is not None, observation=observation)

    @app.get(
        "/api/v1/portfolio/demo-observation",
        response_model=DemoPortfolioResponse,
        dependencies=[Depends(require_operator)],
    )
    def demo_portfolio(response: Response) -> DemoPortfolioResponse:
        response.headers["Cache-Control"] = "no-store"
        observation = (
            read_portfolio(path.parent / "portfolio.json", clock=clock)
            if path is not None
            else DemoPortfolioView(state="UNAVAILABLE")
        )
        return DemoPortfolioResponse(configured=path is not None, observation=observation)
