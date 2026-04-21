"""SPEC-28 §2 — P004: unbounded window frames.

``ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING`` (or the ``RANGE``
equivalent) forces the executor to buffer the entire partition for each row,
turning an otherwise O(log N) sort into O(N²).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P004UnboundedWindowRule(BaseRule):
    id: ClassVar[str] = "P004"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "[O(N) per row] Fully-unbounded window frame"
    confidence_base: ClassVar[float] = 0.85

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        for win in node.ast.find_all(exp.Window):
            if _is_fully_unbounded(win):
                line = node.line_map.get(_line_of(win) or 1, _line_of(win) or 1)
                yield self.make_finding(
                    ctx,
                    line=line,
                    column=1,
                    message="[O(N) per row] Fully-unbounded window frame — specify bounds.",
                    code_context=f"P004:{win.sql()[:120]}",
                )


def _is_fully_unbounded(win: exp.Window) -> bool:
    spec = win.args.get("spec")
    if spec is None:
        return False
    sql = spec.sql().upper()
    return (
        "UNBOUNDED PRECEDING" in sql and "UNBOUNDED FOLLOWING" in sql
    )


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
