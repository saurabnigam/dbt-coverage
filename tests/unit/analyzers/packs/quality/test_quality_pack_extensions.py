"""SPEC-29 §6 — smoke tests for Q004–Q007."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from dbt_coverage.analyzers.packs.quality.q004_missing_description import (
    Q004MissingDescriptionRule,
)
from dbt_coverage.analyzers.packs.quality.q005_undocumented_column import (
    Q005UndocumentedColumnRule,
)
from dbt_coverage.analyzers.packs.quality.q006_naming_convention import (
    Q006NamingConventionRule,
)
from dbt_coverage.analyzers.packs.quality.q007_inconsistent_casing import (
    Q007InconsistentCasingRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.graph.dag import DAG
from dbt_coverage.utils.config import ArchitectureConfig


def _yml(
    name: str,
    *,
    description: str | None = None,
    columns: list[str] | None = None,
    file_path: str = "models/schema.yml",
):
    cols = [SimpleNamespace(name=c) for c in (columns or [])]
    return SimpleNamespace(
        name=name,
        description=description,
        columns=cols,
        file_path=Path(file_path),
        line=1,
    )


def _entry(name: str, path: str, *, yml=None):
    return SimpleNamespace(
        name=name,
        sql_file=SimpleNamespace(path=Path(path)),
        yml_meta=yml,
    )


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
    *,
    project,
    node: ParsedNode | None = None,
    node_id: str | None = None,
    params=None,
) -> RuleContext:
    dag = DAG()
    for nid in project.models:
        dag.add_node(nid)
    graph = SimpleNamespace(dag=dag, get_downstream=lambda nid: dag.children(nid))
    return RuleContext(
        node=node,
        node_id=node_id,
        graph=graph,
        project=project,
        params=params or {},
    )


# ------------------------------------------------------------------------ Q004


def test_q004_fires_on_missing_description() -> None:
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql", yml=_yml("x"))})
    findings = list(Q004MissingDescriptionRule().check(_ctx(project=project)))
    assert findings and "no description" in findings[0].message


def test_q004_silent_when_described() -> None:
    yml = _yml("x", description="Something descriptive.")
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql", yml=yml)})
    assert list(Q004MissingDescriptionRule().check(_ctx(project=project))) == []


# ------------------------------------------------------------------------ Q005


def test_q005_flags_undocumented_column() -> None:
    yml = _yml("x", columns=["id"])
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql", yml=yml)})
    node = _node("select id, name from t", nid="model.demo.x")
    findings = list(
        Q005UndocumentedColumnRule().check(
            _ctx(project=project, node=node, node_id="model.demo.x")
        )
    )
    assert findings and "name" in findings[0].message


def test_q005_respects_ignore_prefix() -> None:
    yml = _yml("x", columns=["id"])
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql", yml=yml)})
    node = _node("select id, _internal from t", nid="model.demo.x")
    findings = list(
        Q005UndocumentedColumnRule().check(
            _ctx(project=project, node=node, node_id="model.demo.x")
        )
    )
    assert findings == []


# ------------------------------------------------------------------------ Q006


def test_q006_fires_on_wrong_layer_prefix() -> None:
    project = SimpleNamespace(
        models={"model.demo.stuff": _entry("stuff", "models/staging/stuff.sql")}
    )
    findings = list(
        Q006NamingConventionRule().check(
            _ctx(project=project, params={"_architecture": ArchitectureConfig()})
        )
    )
    assert findings and "stg_" in findings[0].message


def test_q006_silent_on_correct_prefix() -> None:
    project = SimpleNamespace(
        models={"model.demo.stg_orders": _entry("stg_orders", "models/staging/stg_orders.sql")}
    )
    findings = list(
        Q006NamingConventionRule().check(
            _ctx(project=project, params={"_architecture": ArchitectureConfig()})
        )
    )
    assert findings == []


# ------------------------------------------------------------------------ Q007


def test_q007_flags_mixed_casing() -> None:
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql")})
    node = _node("select user_id, orderDate, sum(amount) as total from t", nid="model.demo.x")
    findings = list(
        Q007InconsistentCasingRule().check(
            _ctx(project=project, node=node, node_id="model.demo.x")
        )
    )
    assert findings and "orderDate" in findings[0].message


def test_q007_silent_when_consistent() -> None:
    project = SimpleNamespace(models={"model.demo.x": _entry("x", "models/x.sql")})
    node = _node("select user_id, order_date from t", nid="model.demo.x")
    findings = list(
        Q007InconsistentCasingRule().check(
            _ctx(project=project, node=node, node_id="model.demo.x")
        )
    )
    assert findings == []


@pytest.mark.parametrize("rid", ["Q004", "Q005", "Q006", "Q007"])
def test_quality_rules_registered(rid: str) -> None:
    from dbt_coverage.analyzers.rule_registry import discover_rules

    assert rid in {getattr(c, "id", None) for c in discover_rules()}
