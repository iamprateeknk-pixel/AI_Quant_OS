import pytest
from pathlib import Path

pytest.importorskip("nautilus_trader")


def test_operator_alert_feed_reports_retained_demotions_after_restart(tmp_path):
    from decimal import Decimal

    from test_shadow_portfolio import forward_bars, shadow_fixture
    from ai_quant_os.api import create_app
    from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
    from ai_quant_os.shadow_reality_gap import RealityGapPolicy
    from ai_quant_os.shadow_store import ShadowStore
    from fastapi.testclient import TestClient

    assignment, shadow_store, candidates, strategies = shadow_fixture(tmp_path)
    policy = RealityGapPolicy(6, 2, 2, Decimal("100"))
    assign_shadow(assignment, shadow_store, candidates, strategies, reality_gap_policy=policy)
    report = observe_shadow(
        assignment.shadow_id,
        "adverse-alert",
        forward_bars(assignment),
        shadow_store,
        candidates,
        strategies,
    )
    assert candidates.get(assignment.candidate.manifest.candidate_id).state == "DEMOTED"

    app = create_app(
        operator_token="operator",
        research_workspace=tmp_path,
    )
    with TestClient(app) as client:
        path = f"/api/v1/paper/shadows/{assignment.shadow_id}/reality-gap-alerts"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-Operator-Token": "operator"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert len(body["alerts"]) == 1
    alert = body["alerts"][0]
    assert alert == {
        "candidate_id": assignment.candidate.manifest.candidate_id,
        "shadow_id": assignment.shadow_id,
        "sequence": report.sequence,
        "report_sha256": report.fingerprint,
        "policy_ref": policy.content_ref,
        "gap_bps": alert["gap_bps"],
        "threshold_bps": "100.000000",
        "state": "DEMOTION_REQUIRED",
    }
    assert Decimal(alert["gap_bps"]) <= -Decimal(alert["threshold_bps"])
    import json
    from jsonschema import validate

    validate(
        body,
        json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/schemas/paper-shadow-reality-gap-alerts-v1.schema.json"
            ).read_text()
        ),
    )
    assert ShadowStore(tmp_path / "shadow.sqlite3").history(assignment.shadow_id) == (report,)
