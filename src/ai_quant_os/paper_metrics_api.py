"""Protected original native HTTP metrics; reading grants no trading authority."""

from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from ai_quant_os.paper_telemetry import PaperTelemetry


def install_paper_metrics_routes(
    app: FastAPI, telemetry: PaperTelemetry, require_operator: Callable
):
    @app.get(
        "/api/v1/observability/metrics",
        response_class=Response,
        dependencies=[Depends(require_operator)],
    )
    def read(
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        try:
            text = telemetry.metrics_text()
        except Exception:
            text = None
        require_operator(x_operator_token, x_operator_session)
        if text is None:
            raise HTTPException(
                status_code=503,
                detail="Original HTTP metrics unavailable",
                headers={"Cache-Control": "no-store"},
            )
        return Response(
            text,
            headers={
                "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Paper-Metrics-Accounting": "VOLATILE_SDK_HTTP_SCOPES",
            },
        )
