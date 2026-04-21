"""SPEC-26 §2 — R004: CTE defined but never referenced in the downstream SELECT.

Fires per dead CTE (one finding each). Uses the same ``exp.With``-aware
reference counter as R003, so a CTE used *only* by another CTE inside
``WITH`` still counts as dead if the outer SELECT never touches the chain.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class R004DeadCteRule(BaseRule):
    id: ClassVar[str] = "R004"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "CTE is defined but never referenced"
    confidence_base: ClassVar[float] = 0.95

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

        # Count references transitively by performing a reachability walk
        # starting from the outer body and following CTE→CTE chains.
        cte_map: dict[str, object] = {}
        for cte in with_.expressions:
            n = _cte_name(cte)
            if n:
                cte_map[n.lower()] = cte

        reachable: set[str] = set()
        body_refs = _table_refs_outside_with(node.ast)
        work = [r for r in body_refs if r in cte_map]
        while work:
            name = work.pop()
            if name in reachable:
                continue
            reachable.add(name)
            inner = cte_map.get(name)
            if inner is None:
                continue
            for tbl in inner.find_all(exp.Table):
                nm = (tbl.name or "").lower()
                if nm in cte_map and nm not in reachable:
                    work.append(nm)

        for cte in with_.expressions:
            name = _cte_name(cte)
            if not name or name.lower() in reachable:
                continue
            line_rendered = _line_of(cte) or 1
            line_source = node.line_map.get(line_rendered, line_rendered)
            yield self.make_finding(
                ctx,
                line=line_source,
                column=1,
                message=f"CTE `{name}` is defined but never referenced; remove to shrink the plan.",
                code_context=f"R004:{name}",
            )


def _cte_name(cte) -> str | None:
    alias = cte.args.get("alias") if hasattr(cte, "args") else None
    try:
        return alias.name if alias is not None else None
    except Exception:
        return None


def _table_refs_outside_with(ast) -> set[str]:
    out: set[str] = set()
    for tbl in ast.find_all(exp.Table):
        cur = tbl.parent
        in_with = False
        while cur is not None:
            if isinstance(cur, exp.With):
                in_with = True
                break
            cur = cur.parent
        if not in_with:
            name = (tbl.name or "").lower()
            if name:
                out.add(name)
    return out


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
