"""Integration test: scan with a simulated ``target/`` artifact bundle."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from dbt_coverage.cli.orchestrator import scan as run_scan

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_dbt_project"


def _write_target(project_root: Path) -> None:
    target = project_root / "target"
    target.mkdir(parents=True, exist_ok=True)

    # Two tests on two different models; one passes, one fails.
    manifest = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json",
            "dbt_version": "1.8.3",
        },
        "nodes": {
            "test.sample_project.not_null_stg_orders_id": {
                "resource_type": "test",
                "unique_id": "test.sample_project.not_null_stg_orders_id",
                "name": "not_null_stg_orders_id",
                "column_name": "id",
                "original_file_path": "models/staging/stg_orders.yml",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.sample_project.stg_orders"]},
            },
            "test.sample_project.singular_fct_orders": {
                "resource_type": "test",
                "unique_id": "test.sample_project.singular_fct_orders",
                "name": "singular_fct_orders",
                "original_file_path": "tests/singular_fct_orders.sql",
                "depends_on": {"nodes": ["model.sample_project.fct_orders"]},
            },
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    run_results = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/run-results/v6.json",
            "dbt_version": "1.8.3",
        },
        "results": [
            {"unique_id": "test.sample_project.not_null_stg_orders_id",
             "status": "pass", "execution_time": 0.1, "message": None},
            {"unique_id": "test.sample_project.singular_fct_orders",
             "status": "pass", "execution_time": 0.2, "message": None},
        ],
    }
    (target / "run_results.json").write_text(json.dumps(run_results), encoding="utf-8")


def test_scan_picks_up_dbt_artifacts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    # Override coverage config to include the new dimensions.
    cli_overrides = {
        "coverage": {
            "dimensions": [
                "test", "doc", "test_meaningful", "test_weighted_cc", "complexity",
            ],
        },
    }
    _write_target(project)
    bundle = run_scan(project, cli_overrides=cli_overrides)
    r = bundle.result

    # At least some TestResults surfaced via the dbt-test adapter.
    assert len(r.test_results) == 2
    assert any(i.adapter == "dbt-test" and i.status == "ok" for i in r.adapter_invocations)

    dims = {m.dimension for m in r.coverage}
    assert {"test_meaningful", "test_weighted_cc", "complexity"} <= dims

    meaningful = next(m for m in r.coverage if m.dimension == "test_meaningful")
    # Only fct_orders got a LOGICAL test → 1/4 models covered.
    assert meaningful.covered == 1
    assert meaningful.total == 4

    # Complexity entries exist for every model.
    assert len(r.complexity) == 4
