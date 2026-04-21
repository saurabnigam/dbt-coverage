"""SPEC-17a — P001: Cross-join / cartesian product without filter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class P001CrossJoinRule(BaseRule):
    id: ClassVar[str] = "P001"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "Cross-join / cartesian product without filter"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    _IGNORE_PRAGMA = "dbtcov:ignore P001"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return

        for select in node.ast.find_all(exp.Select):
            joins = select.args.get("joins") or []
            for join in joins:
                if self._is_exempt(join):
                    continue
                kind = (join.args.get("kind") or join.kind or "").upper() if hasattr(join, "kind") else ""
                kind = kind or (join.args.get("side") or "")
                kind = (kind or "").upper()
                has_on = join.args.get("on") is not None
                has_using = join.args.get("using") is not None

                if kind == "CROSS":
                    if self._has_connecting_where(select, join):
                        continue
                    yield self._make(
                        ctx, select, join,
                        confidence=0.95,
                        message="Explicit CROSS JOIN detected without connecting WHERE predicate",
                    )
                elif not has_on and not has_using:
                    if not self._has_connecting_where(select, join):
                        yield self._make(
                            ctx, select, join,
                            confidence=0.85,
                            message="JOIN without ON/USING and no connecting WHERE predicate",
                        )

    # -------------------------------------------------------------- helpers

    def _is_exempt(self, join: exp.Join) -> bool:
        # LATERAL / UNNEST flatten patterns
        if join.args.get("lateral"):
            return True
        rhs = join.args.get("this") if isinstance(join, exp.Join) else None
        if isinstance(rhs, exp.Unnest) or isinstance(rhs, exp.Lateral):
            return True
        if isinstance(rhs, exp.Subquery):
            inner = rhs.this
            if isinstance(inner, exp.Select):
                has_group = inner.args.get("group") is not None
                projections = inner.expressions or []
                all_agg = bool(projections) and all(
                    isinstance(p, exp.AggFunc) or (isinstance(p, exp.Alias) and isinstance(p.this, exp.AggFunc))
                    for p in projections
                )
                if not has_group and all_agg:
                    return True
        sql = join.sql()
        return self._IGNORE_PRAGMA in sql

    def _has_connecting_where(self, select: exp.Select, join: exp.Join) -> bool:
        where = select.args.get("where")
        if where is None:
            return False
        lhs_tables = self._extract_tables_before(select, join)
        rhs_tables = self._extract_tables_of(join)
        for eq in where.find_all(exp.EQ):
            l_col, r_col = eq.this, eq.expression
            if not (isinstance(l_col, exp.Column) and isinstance(r_col, exp.Column)):
                continue
            l_t = getattr(l_col, "table", None) or ""
            r_t = getattr(r_col, "table", None) or ""
            if l_t and r_t:
                if {l_t, r_t} & lhs_tables and {l_t, r_t} & rhs_tables:
                    return True
        return False

    def _extract_tables_before(self, select: exp.Select, join: exp.Join) -> set[str]:
        names: set[str] = set()
        from_ = select.args.get("from")
        if from_:
            for t in from_.find_all(exp.Table):
                names.add(t.alias_or_name)
        for j in select.args.get("joins") or []:
            if j is join:
                break
            for t in j.find_all(exp.Table):
                names.add(t.alias_or_name)
        return names

    def _extract_tables_of(self, join: exp.Join) -> set[str]:
        return {t.alias_or_name for t in join.find_all(exp.Table)}

    def _make(self, ctx: RuleContext, select, join, confidence: float, message: str) -> Finding:
        line_rendered = _line_of(join) or _line_of(select) or 1
        assert ctx.node is not None
        line_source = ctx.node.line_map.get(line_rendered, line_rendered)
        return self.make_finding(
            ctx,
            line=line_source,
            column=1,
            message=message,
            code_context=_truncate(join.sql()),
            confidence=confidence,
        )


def _line_of(node) -> int | None:
    meta = getattr(node, "meta", None) or {}
    line = meta.get("line")
    if isinstance(line, int):
        return line
    return None


def _truncate(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n]
