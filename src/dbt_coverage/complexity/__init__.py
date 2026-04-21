"""SPEC-19 — SQL + Jinja cyclomatic complexity."""

from __future__ import annotations

from dbt_coverage.core import ParsedNode
from dbt_coverage.core.complexity import ComplexityMetrics

from .jinja_complexity import compute_jinja_cc
from .sql_complexity import compute_sql_cc


def compute_complexity(node: ParsedNode) -> ComplexityMetrics:
    """Combine SQL (AST) + Jinja (raw source) decision points into a ComplexityMetrics."""
    sql = compute_sql_cc(node.ast)
    jinja = compute_jinja_cc(node.source_sql or "")

    cc = 1 + sum(sql.values()) + sum(jinja.values())

    return ComplexityMetrics(
        cc=cc,
        case_arms=sql["case_arms"],
        join_count=sql["join_count"],
        boolean_ops=sql["boolean_ops"],
        set_op_arms=sql["set_op_arms"],
        subqueries=sql["subqueries"],
        iff_count=sql["iff_count"],
        jinja_ifs=jinja["jinja_ifs"],
        jinja_fors=jinja["jinja_fors"],
        parsed_from_ast=bool(node.parse_success and node.ast is not None),
        uncertain=bool(node.render_uncertain or not node.parse_success),
    )


def compute_all(parsed_nodes: dict[str, ParsedNode]) -> dict[str, ComplexityMetrics]:
    return {nid: compute_complexity(n) for nid, n in parsed_nodes.items()}


__all__ = ["compute_complexity", "compute_all", "ComplexityMetrics"]
