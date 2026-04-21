"""SPEC-26 §2 — R003: CTE referenced exactly once → inline candidate."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class R003SingleUseCteRule(BaseRule):
    id: ClassVar[str] = "R003"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "CTE referenced exactly once — consider inlining"
    confidence_base: ClassVar[float] = 0.8

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        with_blocks = list(node.ast.find_all(exp.With))
        if not with_blocks:
            return
        with_ = min(with_blocks, key=_depth)
        if not with_.expressions:
            return

        # Grab the outermost body (everything except the WITH clause).
        body_tables = _collect_table_refs(node.ast, skip_with=True)
        for cte in with_.expressions:
            name = _cte_name(cte)
            if not name:
                continue
            refs = body_tables.get(name.lower(), 0)
            if refs == 1:
                line_rendered = _line_of(cte) or 1
                line_source = node.line_map.get(line_rendered, line_rendered)
                yield self.make_finding(
                    ctx,
                    line=line_source,
                    column=1,
                    message=f"CTE `{name}` is used once; consider inlining.",
                    code_context=f"R003:{name}",
                )


def _cte_name(cte) -> str | None:
    alias = cte.args.get("alias") if hasattr(cte, "args") else None
    if alias is None:
        return None
    try:
        return alias.name
    except Exception:
        return None


def _collect_table_refs(ast, *, skip_with: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tbl in ast.find_all(exp.Table):
        # Skip table refs that live inside the WITH block itself
        # (CTE-to-CTE references).
        if skip_with and _is_in_with(tbl):
            continue
        name = (tbl.name or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _is_in_with(node) -> bool:
    cur = node.parent
    while cur is not None:
        if isinstance(cur, exp.With):
            return True
        cur = cur.parent
    return False


def _line_of(node) -> int | None:
    meta = getattr(node, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None


def _depth(w) -> int:
    d = 0
    cur = w.parent
    while cur is not None:
        d += 1
        cur = cur.parent
    return d
