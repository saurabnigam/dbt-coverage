"""SPEC-22 §6–8 — tests for test_meaningful, test_weighted_cc, complexity dims."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.core import (
    ComplexityMetrics,
    ParsedNode,
    RenderMode,
    TestResult,
    TestStatus,
)
from dbt_coverage.coverage import (
    AggregatorContext,
    compute_all,
    compute_complexity_summary,
    compute_test_cc_weighted_coverage,
    compute_test_meaningful_coverage,
)
from dbt_coverage.scanners import ProjectIndex
from dbt_coverage.utils import DbtcovConfig


def _node(name: str) -> ParsedNode:
    return ParsedNode(
        file_path=Path(f"models/{name}.sql"),
        node_id=f"model.demo.{name}",
        source_sql="select 1",
        rendered_sql="select 1",
        render_mode=RenderMode.MOCK,
    )


def _project() -> ProjectIndex:
    return ProjectIndex(project_root=Path("."), project_name="demo")


def test_test_meaningful_requires_logical_passing() -> None:
    cfg = DbtcovConfig()
    nodes = {f"model.demo.{n}": _node(n) for n in ("a", "b", "c")}
    tr = [
        TestResult(
            test_name="t1", test_kind="singular", model_unique_id="model.demo.a",
            status=TestStatus.PASS, origin="x",
        ),
        TestResult(
            test_name="t2", test_kind="not_null", model_unique_id="model.demo.b",
            status=TestStatus.PASS, origin="x",
        ),
        TestResult(
            test_name="t3", test_kind="singular", model_unique_id="model.demo.c",
            status=TestStatus.FAIL, origin="x",
        ),
    ]
    m = compute_test_meaningful_coverage(nodes, tr, cfg)
    assert m.covered == 1
    assert m.total == 3
    assert m.dimension == "test_meaningful"


def test_test_meaningful_declared_only_fallback() -> None:
    cfg = DbtcovConfig()
    nodes = {f"model.demo.{n}": _node(n) for n in ("a", "b")}
    # No statuses → have_status=False → passing filter bypassed.
    tr = [
        TestResult(
            test_name="t1", test_kind="singular", model_unique_id="model.demo.a",
            status=TestStatus.UNKNOWN, origin="x",
        ),
    ]
    m = compute_test_meaningful_coverage(nodes, tr, cfg)
    assert m.covered == 1
    assert m.total == 2


def test_weighted_cc_heavy_notnull_pulls_score_down() -> None:
    cfg = DbtcovConfig()
    nodes = {f"model.demo.{n}": _node(n) for n in ("a", "b")}
    complexity = {
        "model.demo.a": ComplexityMetrics(cc=3),
        "model.demo.b": ComplexityMetrics(cc=30),
    }
    tr = [
        TestResult(
            test_name="t1", test_kind="singular", model_unique_id="model.demo.a",
            status=TestStatus.PASS, origin="x",
        ),
        TestResult(
            test_name="t2", test_kind="not_null", model_unique_id="model.demo.b",
            status=TestStatus.PASS, origin="x",
        ),
    ]
    m = compute_test_cc_weighted_coverage(nodes, complexity, tr, cfg)
    # num = 1.0*3 + 0.0*30 = 3; den = 33; ratio ≈ 0.091
    assert m.total == 33
    assert m.covered == 3
    assert abs(m.ratio - (3 / 33)) < 1e-6


def test_weighted_cc_missing_complexity_defaults_to_one() -> None:
    cfg = DbtcovConfig()
    nodes = {f"model.demo.{n}": _node(n) for n in ("a", "b")}
    tr = [
        TestResult(
            test_name="t", test_kind="singular", model_unique_id="model.demo.a",
            status=TestStatus.PASS, origin="x",
        ),
    ]
    m = compute_test_cc_weighted_coverage(nodes, {}, tr, cfg)
    assert m.total == 2
    assert m.covered == 1
    assert abs(m.ratio - 0.5) < 1e-6


def test_complexity_dimension_threshold() -> None:
    cfg = DbtcovConfig()
    cfg.complexity.threshold_warn = 15
    cfg.complexity.threshold_block = 30
    nodes = {f"model.demo.{n}": _node(n) for n in ("a", "b", "c")}
    complexity = {
        "model.demo.a": ComplexityMetrics(cc=3),
        "model.demo.b": ComplexityMetrics(cc=30),
        # c missing → treated as cc=1 → under threshold
    }
    m = compute_complexity_summary(nodes, complexity, cfg)
    assert m.total == 3
    assert m.covered == 2  # a and c


def test_aggregator_context_threads_everything() -> None:
    cfg = DbtcovConfig()
    nodes = {f"model.demo.{n}": _node(n) for n in ("a",)}
    ctx = AggregatorContext(
        project=_project(),
        parsed_nodes=nodes,
        complexity={"model.demo.a": ComplexityMetrics(cc=4)},
        test_results=[
            TestResult(
                test_name="s", test_kind="singular",
                model_unique_id="model.demo.a",
                status=TestStatus.PASS, origin="x",
            ),
        ],
        config=cfg,
    )
    out = compute_all(
        ctx, enabled=["test_meaningful", "test_weighted_cc", "complexity"]
    )
    dims = {m.dimension for m in out}
    assert dims == {"test_meaningful", "test_weighted_cc", "complexity"}


def test_failing_test_does_not_cover_when_status_present() -> None:
    cfg = DbtcovConfig()
    nodes = {"model.demo.a": _node("a")}
    tr = [
        TestResult(
            test_name="t", test_kind="singular", model_unique_id="model.demo.a",
            status=TestStatus.FAIL, origin="x",
        ),
    ]
    m = compute_test_meaningful_coverage(nodes, tr, cfg)
    assert m.covered == 0
