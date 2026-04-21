"""SPEC-28 §2 — P005: ``COUNT(DISTINCT …) OVER (…)`` inside a window.

Warehouses evaluate ``COUNT(DISTINCT)`` per row per partition which is O(N)
per row. Suggest pre-aggregating or using ``APPROX_COUNT_DISTINCT`` when
exact precision isn't required.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P005CountDistinctOverRule(BaseRule):
    id: ClassVar[str] = "P005"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = (
        "[O(N) per row] COUNT(DISTINCT …) OVER (…) is expensive; pre-aggregate or approx"
    )
    confidence_base: ClassVar[float] = 0.9

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        for win in node.ast.find_all(exp.Window):
            inner = win.this
            if not isinstance(inner, exp.Count):
                continue
            if not _is_distinct_count(inner):
                continue
            line = node.line_map.get(_line_of(win) or 1, _line_of(win) or 1)
            yield self.make_finding(
                ctx,
                line=line,
                column=1,
                message="[O(N) per row] COUNT(DISTINCT ...) OVER (...) — pre-aggregate or approx.",
                code_context=f"P005:{win.sql()[:120]}",
            )


def _is_distinct_count(count_expr: exp.Count) -> bool:
    if count_expr.args.get("distinct"):
        return True
    inner = count_expr.this
    if isinstance(inner, exp.Distinct):
        return True
    if inner is not None:
        for child in inner.walk():
            if isinstance(child, exp.Distinct):
                return True
    return False


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
