"""SPEC-28 §5 — perf rule pack smoke tests (P002, P006, P007, P008, P009, P010)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from dbt_coverage.analyzers.packs.performance.p002_non_sargable import P002NonSargableRule
from dbt_coverage.analyzers.packs.performance.p004_unbounded_window import (
    P004UnboundedWindowRule,
)
from dbt_coverage.analyzers.packs.performance.p005_count_distinct_over import (
    P005CountDistinctOverRule,
)
from dbt_coverage.analyzers.packs.performance.p007_order_by_without_limit import (
    P007OrderByNoLimitRule,
)
from dbt_coverage.analyzers.packs.performance.p008_deep_cte_chain import (
    P008DeepCteChainRule,
)
from dbt_coverage.analyzers.packs.performance.p009_over_referenced_view import (
    P009OverReferencedViewRule,
)
from dbt_coverage.analyzers.packs.performance.p010_incremental_missing_key import (
    P010IncrementalMissingKeyRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.graph.dag import DAG

# --------------------------------------------------------------------- helpers


def _node(sql: str, *, nid: str = "model.demo.m", path: str = "models/m.sql") -> ParsedNode:
    ast = sqlglot.parse_one(sql)
    return ParsedNode(
        file_path=Path(path),
        node_id=nid,
        source_sql=sql,
        rendered_sql=sql,
        ast=ast,
        render_mode=RenderMode.MOCK,
    )


def _ctx(
    node: ParsedNode | None = None,
    *,
    project=None,
    graph=None,
    params=None,
) -> RuleContext:
    return RuleContext(
        node=node,
        node_id=node.node_id if node else None,
        graph=graph or SimpleNamespace(dag=DAG(), get_downstream=lambda _n: set()),
        project=project or SimpleNamespace(models={}),
        params=params or {},
    )


# ------------------------------------------------------------------------ P002


def test_p002_flags_upper_on_column() -> None:
    node = _node("select 1 from t where UPPER(name) = 'FOO'")
    findings = list(P002NonSargableRule().check(_ctx(node)))
    assert findings and "Non-sargable" in findings[0].message


def test_p002_silent_on_bare_equality() -> None:
    node = _node("select 1 from t where name = 'FOO'")
    assert list(P002NonSargableRule().check(_ctx(node))) == []


# ------------------------------------------------------------------------ P004


def test_p004_flags_unbounded_window() -> None:
    sql = (
        "select sum(x) over (partition by k order by d "
        "rows between unbounded preceding and unbounded following) from t"
    )
    node = _node(sql)
    findings = list(P004UnboundedWindowRule().check(_ctx(node)))
    assert findings


# ------------------------------------------------------------------------ P005


def test_p005_flags_count_distinct_over() -> None:
    node = _node("select count(distinct x) over (partition by y) from t")
    findings = list(P005CountDistinctOverRule().check(_ctx(node)))
    assert findings


# ------------------------------------------------------------------------ P007


def test_p007_flags_order_by_in_cte_without_limit() -> None:
    sql = "with ranked as (select x from t order by x) select * from ranked"
    node = _node(sql)
    findings = list(P007OrderByNoLimitRule().check(_ctx(node)))
    assert findings


def test_p007_silent_when_limit_present() -> None:
    sql = "with ranked as (select x from t order by x limit 10) select * from ranked"
    node = _node(sql)
    assert list(P007OrderByNoLimitRule().check(_ctx(node))) == []


# ------------------------------------------------------------------------ P008


def test_p008_flags_deep_cte_chain() -> None:
    ctes = ", ".join(f"c{i} as (select * from c{i - 1})" for i in range(1, 10))
    sql = f"with c0 as (select 1 as x), {ctes} select * from c9"
    node = _node(sql)
    findings = list(P008DeepCteChainRule().check(_ctx(node, params={"max_depth": 5})))
    assert findings


def test_p008_silent_when_shallow() -> None:
    sql = "with a as (select 1) select * from a"
    node = _node(sql)
    assert list(P008DeepCteChainRule().check(_ctx(node, params={"max_depth": 5}))) == []


# ------------------------------------------------------------------------ P009


def _mk_entry(name: str, path: str, *, materialized: str | None):
    sql = f"{{{{ config(materialized='{materialized}') }}}}\nselect 1" if materialized else "select 1"
    return SimpleNamespace(
        name=name,
        sql_file=SimpleNamespace(path=Path(path), content=sql),
        yml_meta=None,
    )


def test_p009_fires_on_over_referenced_view() -> None:
    entries = {"model.demo.v": _mk_entry("v", "models/v.sql", materialized="view")}
    for i in range(6):
        entries[f"model.demo.d_{i}"] = _mk_entry(f"d_{i}", f"models/d_{i}.sql", materialized="table")
    project = SimpleNamespace(models=entries)
    dag = DAG()
    for nid in entries:
        dag.add_node(nid)
    for i in range(6):
        dag.add_edge("model.demo.v", f"model.demo.d_{i}")
    graph = SimpleNamespace(dag=dag, get_downstream=lambda nid: dag.children(nid))
    findings = list(
        P009OverReferencedViewRule().check(
            _ctx(project=project, graph=graph, params={"threshold": 5})
        )
    )
    assert findings and "View `v`" in findings[0].message


def test_p009_silent_when_under_threshold() -> None:
    entries = {"model.demo.v": _mk_entry("v", "models/v.sql", materialized="view")}
    project = SimpleNamespace(models=entries)
    dag = DAG()
    dag.add_node("model.demo.v")
    graph = SimpleNamespace(dag=dag, get_downstream=lambda nid: dag.children(nid))
    assert (
        list(
            P009OverReferencedViewRule().check(
                _ctx(project=project, graph=graph, params={"threshold": 5})
            )
        )
        == []
    )


# ------------------------------------------------------------------------ P010


def test_p010_fires_when_incremental_missing_unique_key() -> None:
    sql = "{{ config(materialized='incremental') }}\nselect 1"
    entry = SimpleNamespace(
        name="inc",
        sql_file=SimpleNamespace(path=Path("models/inc.sql"), content=sql),
        yml_meta=None,
    )
    project = SimpleNamespace(models={"model.demo.inc": entry})
    findings = list(P010IncrementalMissingKeyRule().check(_ctx(project=project)))
    assert findings
    assert "unique_key" in findings[0].message


def test_p010_silent_when_incremental_is_well_configured() -> None:
    sql = (
        "{{ config(materialized='incremental', unique_key='id', "
        "incremental_strategy='merge') }}\nselect 1"
    )
    entry = SimpleNamespace(
        name="inc",
        sql_file=SimpleNamespace(path=Path("models/inc.sql"), content=sql),
        yml_meta=None,
    )
    project = SimpleNamespace(models={"model.demo.inc": entry})
    assert list(P010IncrementalMissingKeyRule().check(_ctx(project=project))) == []


# ------------------------------------------------------------------------ reg


@pytest.mark.parametrize(
    "rid", ["P002", "P003", "P004", "P005", "P006", "P007", "P008", "P009", "P010"]
)
def test_performance_rules_registered(rid: str) -> None:
    from dbt_coverage.analyzers.rule_registry import discover_rules

    assert rid in {getattr(c, "id", None) for c in discover_rules()}
