"""SPEC-19 §5.1 — SQL AST cyclomatic-complexity visitor.

Walks a sqlglot AST once, tallying decision points. Robust to malformed
input: any exception during traversal yields a zero-tally attribution dict,
never raises.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from sqlglot import expressions as exp
except Exception:  # pragma: no cover - optional at import time
    exp = None  # type: ignore[assignment]


log = logging.getLogger(__name__)


def _zero() -> dict[str, int]:
    return {
        "case_arms": 0,
        "join_count": 0,
        "boolean_ops": 0,
        "set_op_arms": 0,
        "subqueries": 0,
        "iff_count": 0,
    }


def compute_sql_cc(ast: Any | None) -> dict[str, int]:
    """Tally decision points in a sqlglot AST.

    Returns an attribution dict (keys: case_arms, join_count, boolean_ops,
    set_op_arms, subqueries, iff_count). Safe on None and on unexpected
    traversal errors — the caller owns semantic interpretation.
    """
    if ast is None or exp is None:
        return _zero()

    try:
        return _walk(ast)
    except Exception as e:  # defensive: we must never bring down a scan
        log.warning("complexity walk failed: %s", e)
        return _zero()


def _walk(ast: Any) -> dict[str, int]:
    case_arms = 0
    join_count = 0
    boolean_ops = 0
    set_op_arms = 0
    subqueries = 0
    iff_count = 0

    for node in ast.walk():
        if isinstance(node, exp.Case):
            case_arms += len(node.args.get("ifs") or [])

        elif isinstance(node, exp.Join):
            join_count += 1
            on = node.args.get("on")
            if on is not None:
                boolean_ops += _count_bool_ops(on)

        elif isinstance(node, (exp.Where, exp.Having)):
            cond = node.this
            if cond is not None:
                boolean_ops += _count_bool_ops(cond)

        elif isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            set_op_arms += 1

        elif isinstance(node, exp.If):
            # sqlglot represents CASE WHEN arms as exp.If children of exp.Case;
            # those are already counted via case_arms — avoid double counting.
            if not isinstance(node.parent, exp.Case):
                iff_count += 1

        elif isinstance(node, exp.Subquery):
            if _is_correlated(node):
                subqueries += 1

        # exp.Iff is not available in every sqlglot version; check dynamically.
        elif getattr(exp, "Iff", None) is not None and isinstance(node, exp.Iff):  # type: ignore[attr-defined]
            iff_count += 1

    return {
        "case_arms": case_arms,
        "join_count": join_count,
        "boolean_ops": boolean_ops,
        "set_op_arms": set_op_arms,
        "subqueries": subqueries,
        "iff_count": iff_count,
    }


def _count_bool_ops(node: Any) -> int:
    n = 0
    try:
        for child in node.walk():
            if isinstance(child, (exp.And, exp.Or)):
                n += 1
    except Exception:
        return 0
    return n


def _is_correlated(sub: Any) -> bool:
    """A subquery is correlated if any Column inside references a table alias
    defined strictly outside the subquery.

    Heuristic: collect the alias-or-name of every Table reachable from the
    subquery; collect the same for every ancestor (excluding tables inside
    the subquery itself). If any column inside the subquery uses a table
    alias that appears only in the outer set, call it correlated.
    """
    try:
        inner_tables_nodes = set(sub.find_all(exp.Table))
        inner_table_names = {t.alias_or_name for t in inner_tables_nodes}

        outer: set[str] = set()
        ancestor = sub.parent
        while ancestor is not None:
            for t in ancestor.find_all(exp.Table):
                if t in inner_tables_nodes:
                    continue
                outer.add(t.alias_or_name)
            ancestor = ancestor.parent

        for col in sub.find_all(exp.Column):
            tbl = col.table
            if tbl and tbl in outer and tbl not in inner_table_names:
                return True
    except Exception:
        return False
    return False
