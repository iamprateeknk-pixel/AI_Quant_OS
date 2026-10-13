import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.research_candidate_store import CandidateStore
from ai_quant_os.shadow_store import ShadowStore
from ai_quant_os.strategy_registry import StrategyRegistry


def test_shadow_scheduler_requires_persistent_operator_and_research_configuration(tmp_path):
    with pytest.raises(ValueError, match="persistent paper storage"):
        create_app(shadow_scheduler_enabled=True)
    with pytest.raises(ValueError, match="research workspace"):
        create_app(
            shadow_scheduler_enabled=True,
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
        )


def test_shadow_scheduler_status_is_protected_and_disabled_by_default():
    with TestClient(create_app(operator_token="operator")) as client:
        path = "/api/v1/paper/shadows/scheduler"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"X-Operator-Token": "operator"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "schema_version": "1.0",
            "enabled": False,
            "state": "DISABLED",
        }


def test_enabled_shadow_scheduler_starts_and_stops_with_app_lifespan(tmp_path):
    workspace = tmp_path / "research"
    workspace.mkdir()
    ShadowStore(workspace / "shadow.sqlite3")
    CandidateStore(workspace / "candidates.sqlite3")
    strategy_root = workspace / "strategies"
    strategy_root.mkdir()
    StrategyRegistry(strategy_root)
    app = create_app(
        operator_token="operator",
        paper_db_path=tmp_path / "paper.sqlite3",
        research_workspace=workspace,
        shadow_scheduler_enabled=True,
    )

    with TestClient(app) as client:
        assert app.state.shadow_task is not None
        response = client.get(
            "/api/v1/paper/shadows/scheduler",
            headers={"X-Operator-Token": "operator"},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert response.json()["promotion_allowed"] is False
        assert response.json()["may_submit_orders"] is False
    assert app.state.shadow_task is None
