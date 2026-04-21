"""SPEC-28 §2 — P003: self-join with inequality-only predicates.

A self-join whose ``ON`` clause contains inequality operators (``<``, ``>``,
``<=``, ``>=``, ``!=``) but no equality on a unique key yields an O(N²)
intermediate rowset. Fires when the same table appears on both sides and
at least one inequality predicate is present without a matching equality.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P003SelfJoinInequalityRule(BaseRule):
    id: ClassVar[str] = "P003"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "[O(N²)] Self-join on inequality predicates only"
    confidence_base: ClassVar[float] = 0.85

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        for select in node.ast.find_all(exp.Select):
            from_ = select.args.get("from")
            base_tables = (
                [t.name for t in from_.find_all(exp.Table)] if from_ is not None else []
            )
            if not base_tables:
                continue
            for join in select.args.get("joins") or []:
                rhs_tables = [t.name for t in join.find_all(exp.Table)]
                if not any(t in base_tables for t in rhs_tables):
                    continue
                on = join.args.get("on")
                if on is None:
                    continue
                has_eq = any(isinstance(n, exp.EQ) for n in on.walk())
                has_ineq = any(
                    isinstance(n, (exp.GT, exp.LT, exp.GTE, exp.LTE, exp.NEQ))
                    for n in on.walk()
                )
                if has_ineq and not has_eq:
                    line = node.line_map.get(_line_of(join) or 1, _line_of(join) or 1)
                    yield self.make_finding(
                        ctx,
                        line=line,
                        column=1,
                        message="[O(N²)] Self-join with inequality-only predicate.",
                        code_context=f"P003:{join.sql()[:120]}",
                    )


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
