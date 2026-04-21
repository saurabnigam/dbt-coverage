"""SPEC-28 §2 — P007: ``ORDER BY`` inside a CTE/subquery without ``LIMIT``.

Order matters only when followed by ``LIMIT`` or an outer ``ROW_NUMBER``.
Bare ``ORDER BY`` inside a CTE wastes a full sort that the optimiser
frequently can't elide.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P007OrderByNoLimitRule(BaseRule):
    id: ClassVar[str] = "P007"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "[wasted sort] ORDER BY in CTE/subquery without LIMIT"
    confidence_base: ClassVar[float] = 0.85

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        top = node.ast
        for select in node.ast.find_all(exp.Select):
            if select is top:
                continue  # outer SELECT can legitimately sort
            if select.args.get("order") is None:
                continue
            if select.args.get("limit") is not None:
                continue
            line = node.line_map.get(_line_of(select) or 1, _line_of(select) or 1)
            yield self.make_finding(
                ctx,
                line=line,
                column=1,
                message="[wasted sort] ORDER BY inside CTE/subquery without LIMIT.",
                code_context=f"P007:{select.sql()[:120]}",
            )


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
