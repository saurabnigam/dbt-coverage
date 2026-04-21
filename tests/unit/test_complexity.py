"""SPEC-19 — unit tests for the complexity calculator."""

from __future__ import annotations

from pathlib import Path

import sqlglot

from dbt_coverage.complexity import compute_complexity
from dbt_coverage.complexity.jinja_complexity import compute_jinja_cc
from dbt_coverage.complexity.sql_complexity import compute_sql_cc
from dbt_coverage.core import ParsedNode, RenderMode


def _parsed(sql: str, rendered: str | None = None, dialect: str = "postgres") -> ParsedNode:
    text = rendered or sql
    try:
        ast = sqlglot.parse_one(text, read=dialect)
        ok = True
    except Exception:
        ast = None
        ok = False
    return ParsedNode(
        file_path=Path("models/x.sql"),
        node_id="model.demo.x",
        source_sql=sql,
        rendered_sql=text,
        ast=ast,
        render_mode=RenderMode.MOCK,
        parse_success=ok,
    )


def test_plain_select_cc_is_one() -> None:
    node = _parsed("select 1")
    m = compute_complexity(node)
    assert m.cc == 1
    assert m.case_arms == 0
    assert m.join_count == 0
    assert m.iff_count == 0


def test_case_arms_counted_once() -> None:
    sql = "select case when a = 1 then 1 when b = 2 then 2 else 3 end as c from t"
    node = _parsed(sql)
    m = compute_complexity(node)
    assert m.case_arms == 2  # two WHEN arms
    assert m.iff_count == 0  # WHENs must not double-count as IF/IIF


def test_joins_increment_cc() -> None:
    sql = "select * from a join b on a.id = b.id join c on a.id = c.id"
    node = _parsed(sql)
    m = compute_complexity(node)
    assert m.join_count == 2


def test_boolean_ops_counted() -> None:
    sql = "select * from t where a = 1 and b = 2 or c = 3"
    node = _parsed(sql)
    m = compute_complexity(node)
    assert m.boolean_ops == 2


def test_set_op_arms() -> None:
    sql = "select 1 union all select 2 union all select 3"
    node = _parsed(sql)
    m = compute_complexity(node)
    assert m.set_op_arms >= 2


def test_jinja_ifs_and_fors_counted() -> None:
    raw = """
    select
      {% if flag %}a{% else %}b{% endif %},
      {% for x in xs %}{{ x }},{% endfor %}
      {% if a %}1{% elif b %}2{% endif %}
    from t
    """
    jinja = compute_jinja_cc(raw)
    assert jinja["jinja_ifs"] >= 3  # two {% if %} and one {% elif %}
    assert jinja["jinja_fors"] >= 1


def test_compute_complexity_handles_parse_failure() -> None:
    node = ParsedNode(
        file_path=Path("models/x.sql"),
        source_sql="select from",  # broken
        rendered_sql="select from",
        ast=None,
        render_mode=RenderMode.MOCK,
        parse_success=False,
    )
    m = compute_complexity(node)
    assert m.cc >= 1
    assert m.uncertain is True
    assert m.parsed_from_ast is False


def test_sql_cc_returns_all_keys() -> None:
    node = _parsed("select 1")
    d = compute_sql_cc(node.ast)
    # The AST visitor must return every metric key (even when zero) so the
    # downstream ComplexityMetrics builder doesn't KeyError.
    for k in (
        "case_arms",
        "join_count",
        "boolean_ops",
        "set_op_arms",
        "subqueries",
        "iff_count",
    ):
        assert k in d
