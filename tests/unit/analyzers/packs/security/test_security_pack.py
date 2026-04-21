"""SPEC-30 §4 — smoke tests for S001, S002, G001."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from dbt_coverage.analyzers.packs.security.g001_missing_owner import G001MissingOwnerRule
from dbt_coverage.analyzers.packs.security.s001_pii_unmasked import S001PiiUnmaskedRule
from dbt_coverage.analyzers.packs.security.s002_hardcoded_secret import (
    S002HardcodedSecretRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.graph.dag import DAG


def _yml(
    name: str,
    *,
    columns: list[tuple[str, dict]] | None = None,
    meta: dict | None = None,
    file_path: str = "models/schema.yml",
):
    cols = [SimpleNamespace(name=n, meta=m) for n, m in (columns or [])]
    return SimpleNamespace(
        name=name,
        description=None,
        columns=cols,
        meta=meta or {},
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
    try:
        ast = sqlglot.parse_one(sql)
    except Exception:
        ast = None
    return ParsedNode(
        file_path=Path(path),
        node_id=nid,
        source_sql=sql,
        rendered_sql=sql,
        ast=ast,
        render_mode=RenderMode.MOCK,
    )


def _ctx(project, *, node=None, node_id=None, params=None) -> RuleContext:
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


# ------------------------------------------------------------------------ S001


def test_s001_flags_unmasked_pii_column() -> None:
    project = SimpleNamespace(models={"model.demo.u": _entry("u", "models/u.sql")})
    node = _node("select id, ssn from users", nid="model.demo.u")
    findings = list(
        S001PiiUnmaskedRule().check(_ctx(project, node=node, node_id="model.demo.u"))
    )
    assert findings and "ssn" in findings[0].message


def test_s001_silent_when_masked() -> None:
    project = SimpleNamespace(models={"model.demo.u": _entry("u", "models/u.sql")})
    node = _node("select id, mask_ssn(ssn) as ssn from users", nid="model.demo.u")
    findings = list(
        S001PiiUnmaskedRule().check(_ctx(project, node=node, node_id="model.demo.u"))
    )
    assert findings == []


# ------------------------------------------------------------------------ S002


def test_s002_flags_aws_key() -> None:
    project = SimpleNamespace(models={"model.demo.m": _entry("m", "models/m.sql")})
    node = _node("select 'AKIAIOSFODNN7EXAMPLE' as k from t", nid="model.demo.m")
    findings = list(
        S002HardcodedSecretRule().check(_ctx(project, node=node, node_id="model.demo.m"))
    )
    assert findings and "aws_access_key" in findings[0].message


def test_s002_silent_on_plain_sql() -> None:
    project = SimpleNamespace(models={"model.demo.m": _entry("m", "models/m.sql")})
    node = _node("select 'hello' as k from t", nid="model.demo.m")
    findings = list(
        S002HardcodedSecretRule().check(_ctx(project, node=node, node_id="model.demo.m"))
    )
    assert findings == []


# ------------------------------------------------------------------------ G001


def test_g001_flags_missing_owner() -> None:
    project = SimpleNamespace(
        models={"model.demo.x": _entry("x", "models/x.sql", yml=_yml("x"))}
    )
    findings = list(G001MissingOwnerRule().check(_ctx(project)))
    assert findings and "meta.owner" in findings[0].message


def test_g001_silent_when_owner_present() -> None:
    project = SimpleNamespace(
        models={
            "model.demo.x": _entry(
                "x", "models/x.sql", yml=_yml("x", meta={"owner": "finance@demo.co"})
            )
        }
    )
    assert list(G001MissingOwnerRule().check(_ctx(project))) == []


@pytest.mark.parametrize("rid", ["S001", "S002", "G001"])
def test_security_rules_registered(rid: str) -> None:
    from dbt_coverage.analyzers.rule_registry import discover_rules

    assert rid in {getattr(c, "id", None) for c in discover_rules()}
