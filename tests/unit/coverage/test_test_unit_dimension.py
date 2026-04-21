"""SPEC-32 §5 — unit tests for ``test_unit`` coverage dimension."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.core import (
    ParsedNode,
    RenderMode,
    TestKind,
    TestResult,
    TestStatus,
)
from dbt_coverage.coverage.test_unit_coverage import compute_test_unit_coverage


def _node(nid: str) -> ParsedNode:
    return ParsedNode(
        node_id=nid,
        file_path=Path(f"models/{nid.split('.')[-1]}.sql"),
        source_sql="select 1",
        rendered_sql="select 1",
        render_mode=RenderMode.MOCK,
    )


def _tr(name: str, model_uid: str | None, kind: TestKind) -> TestResult:
    return TestResult(
        test_name=name,
        test_kind="unit_test" if kind is TestKind.UNIT else "not_null",
        model_unique_id=model_uid,
        status=TestStatus.PASS,
        origin="dbt-test",
        kind=kind,
    )


def test_zero_when_no_unit_tests() -> None:
    nodes = {"model.demo.a": _node("model.demo.a"), "model.demo.b": _node("model.demo.b")}
    metric = compute_test_unit_coverage(nodes, [])
    assert metric.dimension == "test_unit"
    assert metric.covered == 0
    assert metric.total == 2
    assert metric.ratio == 0.0


def test_counts_models_with_at_least_one_unit_test() -> None:
    nodes = {
        "model.demo.a": _node("model.demo.a"),
        "model.demo.b": _node("model.demo.b"),
        "model.demo.c": _node("model.demo.c"),
    }
    results = [
        _tr("u1", "model.demo.a", TestKind.UNIT),
        _tr("u2", "model.demo.a", TestKind.UNIT),  # same model, shouldn't double count
        _tr("u3", "model.demo.b", TestKind.UNIT),
    ]
    metric = compute_test_unit_coverage(nodes, results)
    assert metric.covered == 2
    assert metric.total == 3
    assert metric.ratio == 2 / 3


def test_ignores_data_tests() -> None:
    nodes = {"model.demo.a": _node("model.demo.a")}
    results = [_tr("not_null_a_id", "model.demo.a", TestKind.DATA)]
    metric = compute_test_unit_coverage(nodes, results)
    assert metric.covered == 0


def test_dbt_below_1_8_emits_note() -> None:
    nodes = {"model.demo.a": _node("model.demo.a")}
    metric = compute_test_unit_coverage(nodes, [], dbt_version="1.7.12")
    assert metric.covered == 0
    assert "dbt_version_below_1_8" in metric.notes


def test_dbt_1_8_no_note() -> None:
    nodes = {"model.demo.a": _node("model.demo.a")}
    metric = compute_test_unit_coverage(nodes, [], dbt_version="1.8.3")
    assert "dbt_version_below_1_8" not in metric.notes


def test_unparseable_dbt_version_no_note() -> None:
    nodes = {"model.demo.a": _node("model.demo.a")}
    metric = compute_test_unit_coverage(nodes, [], dbt_version="rc1")
    assert metric.notes == []


def test_unit_tests_excluded_from_test_meaningful() -> None:
    """SPEC-32 §5 regression check — UNIT tests no longer inflate test_meaningful."""
    from dbt_coverage.coverage.test_meaningful_coverage import (
        compute_test_meaningful_coverage,
    )
    from dbt_coverage.utils import DbtcovConfig

    nodes = {"model.demo.a": _node("model.demo.a")}
    results = [_tr("u1", "model.demo.a", TestKind.UNIT)]
    metric = compute_test_meaningful_coverage(nodes, results, DbtcovConfig())
    assert metric.covered == 0, "unit tests must not count toward test_meaningful"
