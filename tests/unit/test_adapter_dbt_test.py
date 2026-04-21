"""SPEC-23 — unit tests for the dbt-test adapter."""

from __future__ import annotations

import json
from pathlib import Path

from dbt_coverage.adapters import AdapterConfig, DbtTestAdapter
from dbt_coverage.core import TestKind, TestStatus


def _write_manifest(path: Path) -> None:
    data = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json",
            "dbt_version": "1.8.3",
        },
        "nodes": {
            "test.demo.not_null_stg_orders_id": {
                "resource_type": "test",
                "unique_id": "test.demo.not_null_stg_orders_id",
                "name": "not_null_stg_orders_id",
                "column_name": "id",
                "original_file_path": "models/staging/stg_orders.yml",
                "test_metadata": {"name": "not_null", "namespace": None},
                "depends_on": {"nodes": ["model.demo.stg_orders"]},
            },
            "test.demo.assert_positive_totals": {
                "resource_type": "test",
                "unique_id": "test.demo.assert_positive_totals",
                "name": "assert_positive_totals",
                "original_file_path": "tests/assert_positive_totals.sql",
                "depends_on": {"nodes": ["model.demo.fct_orders"]},
            },
            "model.demo.stg_orders": {
                "resource_type": "model",
                "unique_id": "model.demo.stg_orders",
            },
        },
        "unit_tests": {
            "unit_test.demo.check_revenue": {
                "unique_id": "unit_test.demo.check_revenue",
                "name": "check_revenue",
                "original_file_path": "models/marts/_units.yml",
                "model": "model.demo.fct_orders",
                "depends_on": {"nodes": []},
            }
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_run_results(path: Path) -> None:
    data = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/run-results/v6.json",
            "dbt_version": "1.8.3",
        },
        "results": [
            {"unique_id": "test.demo.not_null_stg_orders_id", "status": "pass",
             "execution_time": 0.3, "message": None},
            {"unique_id": "test.demo.assert_positive_totals", "status": "fail",
             "execution_time": 1.1, "message": "1 row violated"},
            {"unique_id": "unit_test.demo.check_revenue", "status": "pass",
             "execution_time": 0.05, "message": None},
            {"unique_id": "model.demo.stg_orders", "status": "success",
             "execution_time": 0.2, "message": None},  # should be ignored
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_dbt_test_adapter_reads_and_joins(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")
    _write_run_results(target / "run_results.json")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    report = adapter.discover(tmp_path, cfg)
    assert report is not None
    result = adapter.read(report, cfg)

    assert result.adapter == "dbt-test"
    assert result.invocation.status == "ok"
    by_name = {tr.test_name: tr for tr in result.test_results}
    assert set(by_name) == {
        "not_null_stg_orders_id",
        "assert_positive_totals",
        "check_revenue",
    }
    assert by_name["not_null_stg_orders_id"].status is TestStatus.PASS
    assert by_name["assert_positive_totals"].status is TestStatus.FAIL
    assert by_name["check_revenue"].test_kind == "unit_test"
    assert by_name["not_null_stg_orders_id"].test_kind == "not_null"
    assert by_name["not_null_stg_orders_id"].model_unique_id == "model.demo.stg_orders"


def test_dbt_test_adapter_no_artifacts(tmp_path: Path) -> None:
    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    assert adapter.discover(tmp_path, cfg) is None
    assert not adapter.is_runnable()


def test_dbt_test_adapter_accepts_success_alias(tmp_path: Path) -> None:
    """dbt 1.8+ writes ``status="success"`` for passing tests — must map to PASS."""
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")

    rr = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/run-results/v6.json",
            "dbt_version": "1.8.3",
        },
        "results": [
            {"unique_id": "test.demo.not_null_stg_orders_id", "status": "success",
             "execution_time": 0.3, "message": None},
            {"unique_id": "test.demo.assert_positive_totals", "status": "success",
             "execution_time": 1.1, "message": None},
        ],
    }
    (target / "run_results.json").write_text(json.dumps(rr), encoding="utf-8")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    report = adapter.discover(tmp_path, cfg)
    result = adapter.read(report, cfg)

    by_name = {tr.test_name: tr for tr in result.test_results}
    assert by_name["not_null_stg_orders_id"].status is TestStatus.PASS
    assert by_name["assert_positive_totals"].status is TestStatus.PASS


def test_dbt_test_adapter_manifest_only(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")
    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    report = adapter.discover(tmp_path, cfg)
    assert report is not None
    result = adapter.read(report, cfg)
    # All test_results should have status UNKNOWN when no run_results.
    assert all(tr.status is TestStatus.UNKNOWN for tr in result.test_results)
    assert len(result.test_results) == 3


# -------------------------------------------------------------------
# SPEC-32 — TestKind classification + executed tracking
# -------------------------------------------------------------------


def test_spec32_classifies_kind_data_vs_unit(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")
    _write_run_results(target / "run_results.json")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    result = adapter.read(adapter.discover(tmp_path, cfg), cfg)

    by_name = {tr.test_name: tr for tr in result.test_results}
    assert by_name["not_null_stg_orders_id"].kind is TestKind.DATA
    assert by_name["assert_positive_totals"].kind is TestKind.DATA
    assert by_name["check_revenue"].kind is TestKind.UNIT


def test_spec32_executed_true_when_run_results_has_entry(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")
    _write_run_results(target / "run_results.json")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    result = adapter.read(adapter.discover(tmp_path, cfg), cfg)

    assert all(tr.executed for tr in result.test_results)


def test_spec32_executed_false_when_missing_from_run_results(tmp_path: Path) -> None:
    """Manifest has 3 tests, run_results has only 1 → 2 should be flagged unexecuted."""
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")

    rr = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/run-results/v6.json",
            "dbt_version": "1.8.3",
        },
        "results": [
            {
                "unique_id": "test.demo.not_null_stg_orders_id",
                "status": "pass",
                "execution_time": 0.3,
                "message": None,
            },
        ],
    }
    (target / "run_results.json").write_text(json.dumps(rr), encoding="utf-8")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    result = adapter.read(adapter.discover(tmp_path, cfg), cfg)

    by_name = {tr.test_name: tr for tr in result.test_results}
    assert by_name["not_null_stg_orders_id"].executed is True
    assert by_name["not_null_stg_orders_id"].status is TestStatus.PASS
    assert by_name["assert_positive_totals"].executed is False
    assert by_name["assert_positive_totals"].status is TestStatus.UNKNOWN
    assert by_name["check_revenue"].executed is False


def test_spec32_manifest_only_marks_all_as_unexecuted(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    result = adapter.read(adapter.discover(tmp_path, cfg), cfg)

    assert all(tr.executed is False for tr in result.test_results)


def test_spec32_invocation_metadata_has_dbt_version(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_manifest(target / "manifest.json")

    adapter = DbtTestAdapter()
    cfg = AdapterConfig()
    result = adapter.read(adapter.discover(tmp_path, cfg), cfg)

    assert result.invocation.metadata.get("dbt_version") == "1.8.3"
