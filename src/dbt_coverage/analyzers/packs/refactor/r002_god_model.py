"""SPEC-26 §2 — R002: God-model detector.

Fires when a single model combines deep CTE nesting, wide projections, and
high cyclomatic complexity. All three dimensions must exceed their thresholds
so we stay specific — a long but simple staging list doesn't trigger.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class R002GodModelRule(BaseRule):
    id: ClassVar[str] = "R002"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = (
        "God-model: too many CTEs, columns, and branches in a single model"
    )
    confidence_base: ClassVar[float] = 0.85

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        cte_thresh = int(ctx.params.get("cte_threshold", 6))
        col_thresh = int(ctx.params.get("column_threshold", 30))
        cc_thresh = int(ctx.params.get("cc_threshold", 25))

        cte_count = _count_ctes(node.ast)
        col_count = _count_top_level_columns(node.ast)
        cc = ctx.complexity.get(ctx.node_id or "", None)
        cc_val = cc.cc if cc is not None else 1

        if cte_count <= cte_thresh or col_count <= col_thresh or cc_val < cc_thresh:
            return

        yield self.make_finding(
            ctx,
            line=1,
            column=1,
            message=(
                f"God-model: {cte_count} CTEs, {col_count} columns, cc={cc_val}. "
                "Extract into staging + marts."
            ),
            code_context=f"R002:{cte_count}:{col_count}:{cc_val}",
        )


def _count_ctes(ast) -> int:
    try:
        with_blocks = list(ast.find_all(exp.With))
    except Exception:
        return 0
    if not with_blocks:
        return 0
    # Only count CTEs in the outermost WITH — nested WITHs (inside a
    # derived table) shouldn't inflate the god-model metric.
    outer = min(with_blocks, key=lambda w: _depth(w))
    try:
        return len(outer.expressions or [])
    except Exception:
        return 0


def _depth(node) -> int:
    d = 0
    cur = node.parent
    while cur is not None:
        d += 1
        cur = cur.parent
    return d


def _count_top_level_columns(ast) -> int:
    if not isinstance(ast, exp.Select):
        # Pick the outermost SELECT if the top is a UNION/etc.
        sels = list(ast.find_all(exp.Select))
        if not sels:
            return 0
        ast = sels[0]
    return len(ast.expressions or [])
