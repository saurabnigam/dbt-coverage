"""SPEC-26 — unit tests for R002–R006 refactor rules."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot

from dbt_coverage.analyzers.packs.refactor.r002_god_model import R002GodModelRule
from dbt_coverage.analyzers.packs.refactor.r003_single_use_cte import (
    R003SingleUseCteRule,
)
from dbt_coverage.analyzers.packs.refactor.r004_dead_cte import R004DeadCteRule
from dbt_coverage.analyzers.packs.refactor.r005_duplicate_expression import (
    R005DuplicateExpressionRule,
)
from dbt_coverage.analyzers.packs.refactor.r006_duplicate_case import (
    R006DuplicateCaseRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ComplexityMetrics, ParsedNode, RenderMode


def _node(sql: str, nid: str = "model.demo.x") -> ParsedNode:
    try:
        ast = sqlglot.parse_one(sql)
    except Exception:
        ast = None
    return ParsedNode(
        file_path=Path(f"models/{nid.split('.')[-1]}.sql"),
        node_id=nid,
        source_sql=sql,
        rendered_sql=sql,
        ast=ast,
        render_mode=RenderMode.MOCK,
        parse_success=ast is not None,
    )


def _ctx(
    node: ParsedNode,
    params: dict | None = None,
    complexity: dict | None = None,
) -> RuleContext:
    return RuleContext(
        node=node,
        node_id=node.node_id,
        graph=None,  # type: ignore[arg-type]
        project=None,  # type: ignore[arg-type]
        params=params or {},
        complexity=complexity or {},
    )


# ------------------------------------------------------------------------ R002


def _wide_ctes_sql(n_ctes: int, n_cols: int) -> str:
    ctes = ",\n".join(f"c{i} as (select 1 as x)" for i in range(n_ctes))
    cols = ", ".join(f"c0.x as c{i}" for i in range(n_cols))
    return f"with {ctes}\nselect {cols} from c0"


def test_r002_god_model_fires_when_all_three_exceed() -> None:
    node = _node(_wide_ctes_sql(7, 31), nid="model.demo.big")
    ctx = _ctx(
        node,
        complexity={"model.demo.big": ComplexityMetrics(cc=26)},
    )
    findings = list(R002GodModelRule().check(ctx))
    assert len(findings) == 1
    assert "God-model" in findings[0].message


def test_r002_god_model_skipped_if_complexity_below_threshold() -> None:
    node = _node(_wide_ctes_sql(7, 31), nid="model.demo.big")
    ctx = _ctx(
        node,
        complexity={"model.demo.big": ComplexityMetrics(cc=10)},
    )
    assert list(R002GodModelRule().check(ctx)) == []


# ------------------------------------------------------------------------ R003


def test_r003_single_use_cte_fires() -> None:
    sql = """
    with stg as (select 1 as x)
    select x from stg
    """
    findings = list(R003SingleUseCteRule().check(_ctx(_node(sql))))
    assert len(findings) == 1
    assert "stg" in findings[0].message


def test_r003_skips_reused_cte() -> None:
    sql = """
    with stg as (select 1 as x)
    select a.x as a_x, b.x as b_x from stg a, stg b
    """
    assert list(R003SingleUseCteRule().check(_ctx(_node(sql)))) == []


# ------------------------------------------------------------------------ R004


def test_r004_dead_cte_fires() -> None:
    sql = """
    with used as (select 1 as x),
         dead as (select 2 as y)
    select x from used
    """
    findings = list(R004DeadCteRule().check(_ctx(_node(sql))))
    assert len(findings) == 1
    assert "dead" in findings[0].message


def test_r004_recognises_transitive_reachability() -> None:
    sql = """
    with a as (select 1 as x),
         b as (select * from a)
    select * from b
    """
    # Neither CTE is dead: b uses a, outer uses b.
    assert list(R004DeadCteRule().check(_ctx(_node(sql)))) == []


# -------------------------------------------------------------------- R005/R006


class _FakeGraph:
    """Bare-minimum graph impl exposing ``canonical_ast`` for R005/R006."""

    def __init__(self, by_nid: dict[str, object]) -> None:
        self._by_nid = by_nid

    def canonical_ast(self, nid: str):
        return self._by_nid.get(nid)


def _project_with_models(models: dict[str, str]):
    entries = {}
    for nid, sql in models.items():
        ast = sqlglot.parse_one(sql)
        entries[nid] = SimpleNamespace(
            name=nid.split(".")[-1],
            sql_file=SimpleNamespace(path=Path(f"models/{nid.split('.')[-1]}.sql")),
            _ast=ast,
        )
    project = SimpleNamespace(models=entries)
    graph = _FakeGraph({nid: entries[nid]._ast for nid in entries})
    return project, graph


def _project_ctx(params: dict, project, graph) -> RuleContext:
    return RuleContext(
        node=None,
        node_id=None,
        graph=graph,  # type: ignore[arg-type]
        project=project,  # type: ignore[arg-type]
        params=params,
    )


def test_r005_fires_when_expression_shared_by_three_models() -> None:
    dup = "cast(order_ts as date) - interval '7' day as reporting_date_prior_week"
    project, graph = _project_with_models(
        {
            "model.demo.a": f"select id, {dup} from x",
            "model.demo.b": f"select id, {dup} from y",
            "model.demo.c": f"select id, {dup} from z",
        }
    )
    findings = list(
        R005DuplicateExpressionRule().check(
            _project_ctx({"min_occurrences": 3, "min_expr_length": 20}, project, graph)
        )
    )
    assert len(findings) == 1
    assert "3 models" in findings[0].message


def test_r005_skips_when_under_min_occurrences() -> None:
    dup = "cast(order_ts as date) - interval '7' day as reporting_date_prior_week"
    project, graph = _project_with_models(
        {
            "model.demo.a": f"select id, {dup} from x",
            "model.demo.b": f"select id, {dup} from y",
        }
    )
    assert (
        list(
            R005DuplicateExpressionRule().check(
                _project_ctx({"min_occurrences": 3, "min_expr_length": 20}, project, graph)
            )
        )
        == []
    )


def test_r006_fires_on_shared_case_ladder() -> None:
    case = (
        "case when tier=1 then 'gold' when tier=2 then 'silver' "
        "when tier=3 then 'bronze' else 'none' end as label"
    )
    project, graph = _project_with_models(
        {
            "model.demo.a": f"select id, {case} from x",
            "model.demo.b": f"select id, {case} from y",
            "model.demo.c": f"select id, {case} from z",
        }
    )
    findings = list(
        R006DuplicateCaseRule().check(
            _project_ctx({"min_occurrences": 3, "min_arms": 3}, project, graph)
        )
    )
    assert len(findings) == 1
    assert "3 models" in findings[0].message


def test_r006_ignores_trivial_two_arm_case() -> None:
    case = "case when x=1 then 'a' else 'b' end as label"
    project, graph = _project_with_models(
        {
            "model.demo.a": f"select id, {case} from x",
            "model.demo.b": f"select id, {case} from y",
            "model.demo.c": f"select id, {case} from z",
        }
    )
    assert (
        list(
            R006DuplicateCaseRule().check(
                _project_ctx({"min_occurrences": 3, "min_arms": 3}, project, graph)
            )
        )
        == []
    )


# Smoke test: all five rules are discoverable from the registry.


def test_registry_registers_all_refactor_rules() -> None:
    from dbt_coverage.analyzers.rule_registry import discover_rules

    ids = {getattr(c, "id", None) for c in discover_rules()}
    assert {"R002", "R003", "R004", "R005", "R006"} <= ids


@pytest.mark.parametrize(
    "rule_cls",
    [
        R002GodModelRule,
        R003SingleUseCteRule,
        R004DeadCteRule,
        R005DuplicateExpressionRule,
        R006DuplicateCaseRule,
    ],
)
def test_all_rules_have_refactor_category(rule_cls) -> None:
    from dbt_coverage.core import Category

    assert rule_cls.category is Category.REFACTOR
