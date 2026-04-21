"""SPEC-29 §3 — Q005: projection column missing a schema.yml entry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class Q005UndocumentedColumnRule(BaseRule):
    id: ClassVar[str] = "Q005"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Column missing from schema.yml `columns:` list"
    confidence_base: ClassVar[float] = 0.85
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None or ctx.node_id is None:
            return
        entry = ctx.project.models.get(ctx.node_id)
        if entry is None:
            return
        yml = getattr(entry, "yml_meta", None)
        declared = {c.name.lower() for c in (yml.columns if yml else [])}
        ignore_prefixes = tuple(ctx.params.get("ignore_prefixes") or ("_",))

        seen_alias: set[str] = set()
        for select in node.ast.find_all(exp.Select):
            # Only consider the outermost SELECT: CTE projections aren't the
            # final model surface.
            if _is_inside_cte(select):
                continue
            for projection in select.expressions or []:
                alias = _alias_of(projection)
                if not alias:
                    continue
                if alias.lower() in seen_alias:
                    continue
                seen_alias.add(alias.lower())
                if alias.lower() in declared:
                    continue
                if alias.startswith(ignore_prefixes):
                    continue
                line = node.line_map.get(_line_of(projection) or 1, 1)
                yield self.make_finding(
                    ctx,
                    line=line,
                    column=1,
                    message=(
                        f"Column `{alias}` is projected by `{entry.name}` but missing "
                        "from schema.yml `columns:`."
                    ),
                    code_context=f"Q005:{alias}",
                )
            break  # only need the outermost SELECT's projections


def _is_inside_cte(select: exp.Select) -> bool:
    parent = select.parent
    while parent is not None:
        if isinstance(parent, exp.CTE):
            return True
        parent = parent.parent
    return False


def _alias_of(projection) -> str | None:
    if isinstance(projection, exp.Alias):
        return projection.alias_or_name
    if isinstance(projection, exp.Column):
        return projection.name
    if isinstance(projection, exp.Star):
        return None
    alias = getattr(projection, "alias_or_name", None)
    return alias or None


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
