"""SPEC-27 §4 — architecture rules A001–A005 and the layers classifier."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from dbt_coverage.analyzers.packs.architecture.a001_layer_violation import (
    A001LayerViolationRule,
)
from dbt_coverage.analyzers.packs.architecture.a002_fan_in import A002FanInRule
from dbt_coverage.analyzers.packs.architecture.a003_direct_source import (
    A003DirectSourceBypassRule,
)
from dbt_coverage.analyzers.packs.architecture.a004_cycle import A004CircularDepRule
from dbt_coverage.analyzers.packs.architecture.a005_leaky_abstraction import (
    A005LeakyAbstractionRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.graph.dag import DAG
from dbt_coverage.graph.layers import classify_layer, edge_is_allowed
from dbt_coverage.utils.config import ArchitectureConfig

# ------------------------------------------------------------------ layers util


def test_classify_layer_matches_name_prefix() -> None:
    cfg = ArchitectureConfig()
    assert classify_layer("model.demo.stg_orders", "models/stg_orders.sql", cfg) == "staging"
    assert classify_layer("model.demo.fct_orders", "models/marts/fct_orders.sql", cfg) == "mart"


def test_classify_layer_matches_path_glob() -> None:
    cfg = ArchitectureConfig()
    assert (
        classify_layer("model.demo.orders", "models/staging/orders.sql", cfg) == "staging"
    )


def test_edge_is_allowed_defaults() -> None:
    cfg = ArchitectureConfig()
    assert edge_is_allowed("staging", "mart", cfg)
    assert not edge_is_allowed("mart", "staging", cfg)
    # Unknown layers → conservative (allow).
    assert edge_is_allowed(None, "mart", cfg)


# ------------------------------------------------------------------ fixtures


def _entry(name: str, path: str):
    return SimpleNamespace(
        name=name,
        sql_file=SimpleNamespace(path=Path(path)),
    )


def _project(entries: dict[str, tuple[str, str]]):
    return SimpleNamespace(
        models={nid: _entry(name, path) for nid, (name, path) in entries.items()},
    )


def _graph(entries: dict[str, tuple[str, str]], edges: list[tuple[str, str]]):
    dag = DAG()
    for nid in entries:
        dag.add_node(nid)
    for a, b in edges:
        dag.add_edge(a, b)
    g = SimpleNamespace(
        dag=dag,
        get_upstream=lambda nid: dag.parents(nid),
        get_downstream=lambda nid: dag.children(nid),
    )
    return g


def _ctx(
    *,
    project,
    graph,
    params: dict | None = None,
    node: ParsedNode | None = None,
    node_id: str | None = None,
) -> RuleContext:
    return RuleContext(
        node=node,
        node_id=node_id,
        graph=graph,
        project=project,
        params=params or {},
    )


def _node(path: str, nid: str, sql: str = "select 1", sources=()) -> ParsedNode:
    return ParsedNode(
        file_path=Path(path),
        node_id=nid,
        source_sql=sql,
        rendered_sql=sql,
        ast=sqlglot.parse_one(sql),
        render_mode=RenderMode.MOCK,
        sources=list(sources),
    )


# ------------------------------------------------------------------------ A001


def test_a001_fires_on_mart_to_staging_edge() -> None:
    entries = {
        "model.demo.stg_a": ("stg_a", "models/staging/stg_a.sql"),
        "model.demo.fct_b": ("fct_b", "models/marts/fct_b.sql"),
    }
    project = _project(entries)
    # Illegal edge: fct_b → stg_a (mart → staging).
    graph = _graph(entries, [("model.demo.fct_b", "model.demo.stg_a")])
    arch = ArchitectureConfig()
    findings = list(
        A001LayerViolationRule().check(
            _ctx(project=project, graph=graph, params={"_architecture": arch})
        )
    )
    assert any("Layer violation" in f.message for f in findings)


def test_a001_silent_on_allowed_edge() -> None:
    entries = {
        "model.demo.stg_a": ("stg_a", "models/staging/stg_a.sql"),
        "model.demo.fct_b": ("fct_b", "models/marts/fct_b.sql"),
    }
    project = _project(entries)
    graph = _graph(entries, [("model.demo.stg_a", "model.demo.fct_b")])
    arch = ArchitectureConfig()
    findings = list(
        A001LayerViolationRule().check(
            _ctx(project=project, graph=graph, params={"_architecture": arch})
        )
    )
    assert findings == []


# ------------------------------------------------------------------------ A002


def test_a002_fan_in_fires_over_threshold() -> None:
    entries = {f"model.demo.src_{i}": (f"src_{i}", f"models/src_{i}.sql") for i in range(16)}
    entries["model.demo.big"] = ("big", "models/big.sql")
    project = _project(entries)
    edges = [(f"model.demo.src_{i}", "model.demo.big") for i in range(16)]
    graph = _graph(entries, edges)
    findings = list(
        A002FanInRule().check(_ctx(project=project, graph=graph, params={"threshold": 15}))
    )
    assert any("16 upstream" in f.message for f in findings)


def test_a002_fan_in_silent_under_threshold() -> None:
    entries = {"model.demo.a": ("a", "models/a.sql"), "model.demo.b": ("b", "models/b.sql")}
    project = _project(entries)
    graph = _graph(entries, [("model.demo.a", "model.demo.b")])
    findings = list(
        A002FanInRule().check(_ctx(project=project, graph=graph, params={"threshold": 15}))
    )
    assert findings == []


# ------------------------------------------------------------------------ A003


def test_a003_fires_when_intermediate_reads_source() -> None:
    entries = {
        "model.demo.int_a": ("int_a", "models/intermediate/int_a.sql"),
    }
    project = _project(entries)
    graph = _graph(entries, [])
    node = _node(
        "models/intermediate/int_a.sql",
        "model.demo.int_a",
        sources=[("raw", "orders")],
    )
    arch = ArchitectureConfig()
    findings = list(
        A003DirectSourceBypassRule().check(
            _ctx(
                project=project,
                graph=graph,
                node=node,
                node_id="model.demo.int_a",
                params={"_architecture": arch},
            )
        )
    )
    assert any("reads sources directly" in f.message for f in findings)


def test_a003_silent_when_staging_reads_source() -> None:
    entries = {"model.demo.stg_a": ("stg_a", "models/staging/stg_a.sql")}
    project = _project(entries)
    graph = _graph(entries, [])
    node = _node(
        "models/staging/stg_a.sql",
        "model.demo.stg_a",
        sources=[("raw", "orders")],
    )
    arch = ArchitectureConfig()
    findings = list(
        A003DirectSourceBypassRule().check(
            _ctx(
                project=project,
                graph=graph,
                node=node,
                node_id="model.demo.stg_a",
                params={"_architecture": arch},
            )
        )
    )
    assert findings == []


# ------------------------------------------------------------------------ A004


def test_a004_detects_cycle() -> None:
    entries = {
        "model.demo.a": ("a", "models/a.sql"),
        "model.demo.b": ("b", "models/b.sql"),
    }
    project = _project(entries)
    graph = _graph(
        entries, [("model.demo.a", "model.demo.b"), ("model.demo.b", "model.demo.a")]
    )
    findings = list(A004CircularDepRule().check(_ctx(project=project, graph=graph)))
    assert len(findings) >= 1
    assert "Circular" in findings[0].message


def test_a004_silent_on_acyclic_graph() -> None:
    entries = {"model.demo.a": ("a", "a.sql"), "model.demo.b": ("b", "b.sql")}
    project = _project(entries)
    graph = _graph(entries, [("model.demo.a", "model.demo.b")])
    assert list(A004CircularDepRule().check(_ctx(project=project, graph=graph))) == []


# ------------------------------------------------------------------------ A005


def test_a005_flags_non_snake_projection() -> None:
    entries = {"model.demo.stg_a": ("stg_a", "models/staging/stg_a.sql")}
    project = _project(entries)
    graph = _graph(entries, [])
    node = _node(
        "models/staging/stg_a.sql",
        "model.demo.stg_a",
        sql="select userId from raw",
    )
    arch = ArchitectureConfig()
    findings = list(
        A005LeakyAbstractionRule().check(
            _ctx(
                project=project,
                graph=graph,
                node=node,
                node_id="model.demo.stg_a",
                params={"_architecture": arch},
            )
        )
    )
    assert any("userId" in f.message for f in findings)


def test_a005_silent_on_snake_case() -> None:
    entries = {"model.demo.stg_a": ("stg_a", "models/staging/stg_a.sql")}
    project = _project(entries)
    graph = _graph(entries, [])
    node = _node(
        "models/staging/stg_a.sql",
        "model.demo.stg_a",
        sql="select user_id as user_id from raw",
    )
    arch = ArchitectureConfig()
    findings = list(
        A005LeakyAbstractionRule().check(
            _ctx(
                project=project,
                graph=graph,
                node=node,
                node_id="model.demo.stg_a",
                params={"_architecture": arch},
            )
        )
    )
    assert findings == []


@pytest.mark.parametrize("rid", ["A001", "A002", "A003", "A004", "A005"])
def test_architecture_rules_registered(rid: str) -> None:
    from dbt_coverage.analyzers.rule_registry import discover_rules

    assert rid in {getattr(c, "id", None) for c in discover_rules()}
