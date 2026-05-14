from __future__ import annotations

from pathlib import Path

import pytest

from dbt_coverage_ui.db import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db.sqlite")


def test_create_and_list_project(store: Store) -> None:
    p = store.create_project("alpha", "/tmp/alpha")
    assert p["name"] == "alpha"
    assert len(p["id"]) == 12
    assert store.get_project_by_name("alpha")["path"] == "/tmp/alpha"
    listed = store.list_projects()
    assert [r["name"] for r in listed] == ["alpha"]
    # last-run fields are NULL until a run finishes
    assert listed[0]["last_score"] is None


def test_run_lifecycle_updates_last_run(store: Store) -> None:
    p = store.create_project("beta", "/tmp/beta")
    rid = store.create_run(p["id"], "/tmp/beta/run1")
    assert store.get_run(rid)["status"] == "running"

    store.finish_run(
        rid,
        {
            "project_id": p["id"],
            "status": "success",
            "render_mode": "COMPILED",
            "score_mean": 75.0,
            "score_median": 80.0,
            "findings_total": 100,
            "findings_critical": 5,
            "findings_major": 30,
            "findings_minor": 65,
            "models_total": 10,
            "models_at_risk": 2,
            "parse_failed": 0,
            "coverage_test": 0.9,
            "coverage_doc": 0.5,
            "coverage_test_unit": 0.1,
            "coverage_test_meaningful": 0.4,
            "coverage_complexity": 0.85,
            "duration_ms": 12000,
        },
    )
    run = store.get_run(rid)
    assert run["status"] == "success"
    assert run["score_mean"] == 75.0
    listed = store.list_projects()
    assert listed[0]["last_score"] == 75.0
    assert listed[0]["last_findings"] == 100


def test_failed_run_does_not_set_last_run(store: Store) -> None:
    p = store.create_project("gamma", "/tmp/gamma")
    rid = store.create_run(p["id"], "/tmp/gamma/run1")
    store.finish_run(
        rid,
        {
            "project_id": p["id"],
            "status": "failed",
            "error_message": "boom",
        },
    )
    listed = store.list_projects()
    assert listed[0]["last_score"] is None
    assert store.get_run(rid)["error_message"] == "boom"


def test_delete_project_cascades_runs(store: Store) -> None:
    p = store.create_project("delta", "/tmp/delta")
    rid = store.create_run(p["id"], "/tmp/delta/run1")
    store.delete_project(p["id"])
    assert store.get_project(p["id"]) is None
    assert store.get_run(rid) is None
