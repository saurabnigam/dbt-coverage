"""SPEC-28 §2 — P008: deep CTE chain impedes the optimiser."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P008DeepCteChainRule(BaseRule):
    id: ClassVar[str] = "P008"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "[optimiser barrier] Deep CTE chain"
    confidence_base: ClassVar[float] = 0.8

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        max_depth = int(ctx.params.get("max_depth", 8))
        for with_ in node.ast.find_all(exp.With):
            n = len(with_.expressions or [])
            if n > max_depth:
                line = node.line_map.get(_line_of(with_) or 1, _line_of(with_) or 1)
                yield self.make_finding(
                    ctx,
                    line=line,
                    column=1,
                    message=(
                        f"[optimiser barrier] CTE chain depth={n} exceeds {max_depth}; "
                        "consider splitting into separate models."
                    ),
                    code_context=f"P008:{n}",
                )


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
