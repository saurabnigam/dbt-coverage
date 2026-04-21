"""SPEC-18 §4.4 — declared-vs-actual column diff."""

from __future__ import annotations

from typing import Any

from sqlglot import expressions as exp

from dbt_coverage.core import ColumnDiff
from dbt_coverage.scanners import YamlModelMeta


def extract_select_columns(ast: Any) -> list[str] | None:
    """Return top-level SELECT projection names, or None for SELECT *."""
    if ast is None:
        return None
    select = ast if isinstance(ast, exp.Select) else ast.find(exp.Select)
    if select is None:
        return None

    out: list[str] = []
    for proj in select.expressions or []:
        if isinstance(proj, exp.Star):
            return None
        name = proj.alias_or_name
        if name:
            out.append(name)
    return out


def declared_vs_actual(
    yml_meta: YamlModelMeta | None,
    ast: Any | None,
) -> ColumnDiff | None:
    if yml_meta is None or ast is None:
        return None
    actual = extract_select_columns(ast)
    if actual is None:
        return None
    declared = [c.name for c in yml_meta.columns]
    declared_set = set(declared)
    actual_set = set(actual)
    return ColumnDiff(
        declared_only=sorted(declared_set - actual_set),
        actual_only=sorted(actual_set - declared_set),
        matching=sorted(declared_set & actual_set),
    )
