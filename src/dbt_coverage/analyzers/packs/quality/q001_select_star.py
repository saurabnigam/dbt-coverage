"""SPEC-08a — Q001: SELECT * in non-source model or CTE."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class Q001SelectStarRule(BaseRule):
    id: ClassVar[str] = "Q001"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "SELECT * in non-source model or CTE"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return

        seen_lines: set[int] = set()
        for select in node.ast.find_all(exp.Select):
            for expr_node in select.expressions or []:
                if not isinstance(expr_node, exp.Star):
                    continue
                if self._is_exempt(select, expr_node):
                    continue
                line_rendered = _line_of(expr_node) or _line_of(select) or 1
                line_source = node.line_map.get(line_rendered, line_rendered)
                # Dedupe adjacent STAR findings on same source line.
                if line_source in seen_lines:
                    continue
                seen_lines.add(line_source)
                yield self.make_finding(
                    ctx,
                    line=line_source,
                    column=1,
                    message="SELECT * in model or CTE; list columns explicitly",
                    code_context=_truncate(select.sql()),
                )

    # -------------------------------------------------------------- helpers

    def _is_exempt(self, select: exp.Select, star: exp.Star) -> bool:
        if star.args.get("except"):
            return True
        from_ = select.args.get("from")
        joins = select.args.get("joins") or []
        ctes = select.args.get("with")
        if from_ and not joins and ctes is None:
            tables = list(from_.find_all(exp.Table))
            if len(tables) == 1 and tables[0].name.startswith("__SRC_"):
                return True
        return False


def _line_of(node) -> int | None:
    meta = getattr(node, "meta", None) or {}
    line = meta.get("line")
    if isinstance(line, int):
        return line
    return None


def _truncate(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n]
