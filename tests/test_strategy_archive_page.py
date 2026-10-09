from fastapi.testclient import TestClient

from ai_quant_os.api import create_app


def archive(root):
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin
    from ai_quant_os.strategy_contracts import StrategyGenome

    registry = StrategyRegistry(root / "strategies")
    for ref in ("buy-and-hold@1.0.0", "moving-average-cross@1.0.0"):
        prepare_builtin(StrategyGenome(ref, "BAR"), registry)
    return registry


def test_strategy_page_shows_checked_archives_without_active_claims(tmp_path):
    registry = archive(tmp_path)
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        url = "/api/v1/research/strategies/page"
        headers = {"X-Operator-Token": "operator"}
        assert client.get(url).status_code == 401
        response = client.get(url, params={"limit": 1}, headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 2 and page["next_offset"] == 1
        row = page["strategies"][0]
        assert row["state"] == "ARCHIVED_IMPLEMENTATION"
        assert row["promotion_allowed"] is False
        assert row["implementation_sha256"] == registry.get(row["strategy_ref"]).fingerprint
        assert "content" not in response.text and "class BuyAndHold" not in response.text
        assert client.get(url, params={"offset": 1}, headers=headers).status_code == 422
        assert client.get(url, params={"head": "0" * 64}, headers=headers).status_code == 409
        continued = client.get(
            url, params={"offset": 1, "limit": 1, "head": page["head_hash"]}, headers=headers
        )
        assert continued.status_code == 200
        assert continued.json()["strategies"][0]["strategy_ref"] != row["strategy_ref"]


def test_corrupt_offpage_source_withholds_strategy_catalog(tmp_path):
    registry = archive(tmp_path)
    source = registry.get("moving-average-cross@1.0.0").source_files[0]
    (registry.root / "objects" / (source.sha256 + ".py")).write_bytes(b"forged source")
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        response = client.get(
            "/api/v1/research/strategies/page?limit=1", headers={"X-Operator-Token": "operator"}
        )
        assert response.status_code == 503 and "forged" not in response.text


def test_missing_strategy_archive_is_unavailable_without_creating_it(tmp_path):
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        assert (
            client.get(
                "/api/v1/research/strategies/page", headers={"X-Operator-Token": "operator"}
            ).status_code
            == 503
        )
    assert not (tmp_path / "strategies").exists()


def test_catalog_changes_during_observation_withhold_records(tmp_path, monkeypatch):
    from dataclasses import replace
    from ai_quant_os.strategy_registry import StrategyRegistry

    registry = archive(tmp_path)
    original = StrategyRegistry._catalog_manifests
    calls = 0

    def changed(self):
        nonlocal calls
        rows = original(self)
        calls += 1
        if calls == 1:
            registry.register(replace(rows[0][0], strategy_ref="buy-and-hold@2.0.0"))
        return rows

    monkeypatch.setattr(StrategyRegistry, "_catalog_manifests", changed)
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        response = client.get(
            "/api/v1/research/strategies/page", headers={"X-Operator-Token": "operator"}
        )
        assert response.status_code == 409 and "strategies" not in response.json()


def test_catalog_has_bounded_inventory_and_an_honest_empty_archive(tmp_path):
    import pytest
    from ai_quant_os.strategy_registry import StrategyRegistry

    root = tmp_path / "strategies"
    root.mkdir()
    registry = StrategyRegistry(root)
    assert registry.catalog()[1] == ()
    for number in range(1001):
        (root / f"sample-{number}@1.0.0.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="capacity"):
        registry.catalog()


def test_strategy_preview_schema_matches_and_fits_transport():
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from ai_quant_os.strategy_archive_views import StrategyArchivePage, strategy_preview

    implementation = SimpleNamespace(
        strategy_ref="sample@1.0.0",
        input_kind="BAR",
        fingerprint="a" * 64,
        parameter_defaults=tuple(("p" + chr(97 + number), 4096) for number in range(16)),
        source_files=tuple(SimpleNamespace(path="a" * 512, sha256="b" * 64) for _ in range(16)),
    )
    row = strategy_preview(implementation)
    assert row.details_truncated is True
    page = StrategyArchivePage(
        head_hash="c" * 64,
        total=10,
        offset=0,
        limit=10,
        next_offset=None,
        strategies=tuple(
            row.model_copy(update={"strategy_ref": f"sample-{number}@1.0.0"})
            for number in range(10)
        ),
    )
    assert len(page.model_dump_json().encode()) < 131072
    assert (
        json.loads(Path("docs/schemas/strategy-archive-page.schema.json").read_text())
        == StrategyArchivePage.model_json_schema()
    )
