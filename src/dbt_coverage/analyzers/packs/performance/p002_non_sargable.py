"""SPEC-28 §2 — P002: non-sargable predicate on a column.

A predicate is non-sargable when it wraps the column in a function so the
optimiser can't use an index. Common culprits: ``UPPER(x) = 'FOO'``,
``CAST(x AS date) = '2024-01-01'``, ``DATE(x) = '…'``, ``LOWER(x) = '…'``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_WRAPPERS = (exp.Upper, exp.Lower, exp.Cast, exp.TsOrDsToDate, exp.Trim)


class P002NonSargableRule(BaseRule):
    id: ClassVar[str] = "P002"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "[O(N) scan] Non-sargable predicate wraps column in a function"
    confidence_base: ClassVar[float] = 0.75

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        for where in node.ast.find_all(exp.Where):
            for pred in where.find_all((exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE)):
                for side in (pred.this, pred.expression):
                    if isinstance(side, _WRAPPERS) and _wraps_column(side):
                        line = node.line_map.get(_line_of(pred) or 1, _line_of(pred) or 1)
                        yield self.make_finding(
                            ctx,
                            line=line,
                            column=1,
                            message=(
                                "[O(N) scan] Non-sargable predicate — move the function "
                                "to the literal side or pre-compute."
                            ),
                            code_context=f"P002:{pred.sql()[:120]}",
                        )
                        break


def _wraps_column(node) -> bool:
    for n in node.walk():
        if isinstance(n, exp.Column):
            return True
    return False


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
