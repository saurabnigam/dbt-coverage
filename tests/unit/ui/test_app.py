from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from dbt_coverage_ui.app import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(data_root=tmp_path)
    return TestClient(app)


@pytest.fixture()
def sample_dbt_path(tmp_path: Path) -> Path:
    p = tmp_path / "fake_dbt"
    p.mkdir()
    (p / "dbt_project.yml").write_text("name: fake\nversion: 1.0\nprofile: fake\n")
    return p


def test_meta_endpoint(client: TestClient) -> None:
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert "Q001" in body["rules"]
    assert "test" in body["dimensions"]
    assert "render.mode" in body["config_fields"]


def test_register_and_list_project(client: TestClient, sample_dbt_path: Path) -> None:
    r = client.post("/api/projects", json={"name": "demo", "path": str(sample_dbt_path)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r2 = client.get("/api/projects")
    assert r2.status_code == 200
    assert any(p["id"] == pid for p in r2.json())

    r3 = client.get(f"/api/projects/{pid}")
    assert r3.status_code == 200
    assert r3.json()["runs"] == []


def test_register_rejects_missing_path(client: TestClient) -> None:
    r = client.post("/api/projects", json={"name": "x", "path": "/does/not/exist"})
    assert r.status_code == 400


def test_register_rejects_duplicate_name(client: TestClient, sample_dbt_path: Path) -> None:
    client.post("/api/projects", json={"name": "dup", "path": str(sample_dbt_path)})
    r = client.post("/api/projects", json={"name": "dup", "path": str(sample_dbt_path)})
    assert r.status_code == 409


def test_config_get_and_put(client: TestClient, sample_dbt_path: Path) -> None:
    pid = client.post("/api/projects", json={"name": "cfg", "path": str(sample_dbt_path)}).json()["id"]

    r = client.get(f"/api/projects/{pid}/config")
    assert r.status_code == 200
    assert r.json()["exists"] is False

    yaml_text = "version: 1\nrender:\n  mode: AUTO\n"
    r2 = client.put(f"/api/projects/{pid}/config", json={"yaml": yaml_text})
    assert r2.status_code == 200, r2.text
    assert (sample_dbt_path / "dbtcov.yml").read_text() == yaml_text

    r3 = client.get(f"/api/projects/{pid}/config")
    assert r3.json()["exists"] is True


def test_config_put_rejects_invalid_yaml(client: TestClient, sample_dbt_path: Path) -> None:
    pid = client.post("/api/projects", json={"name": "bad", "path": str(sample_dbt_path)}).json()["id"]
    r = client.put(f"/api/projects/{pid}/config", json={"yaml": "key: [unclosed"})
    assert r.status_code == 400


def test_index_html_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "dbtcov" in r.text
