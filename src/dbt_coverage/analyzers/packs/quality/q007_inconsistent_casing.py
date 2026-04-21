"""SPEC-29 §5 — Q007: projection list mixes snake_case with camel/Pascal."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class Q007InconsistentCasingRule(BaseRule):
    id: ClassVar[str] = "Q007"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Model projection mixes snake_case with camelCase/PascalCase"
    confidence_base: ClassVar[float] = 0.8
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        outer = _first_outer_select(node.ast)
        if outer is None:
            return

        snake: list[str] = []
        camel: list[str] = []
        for projection in outer.expressions or []:
            alias = _alias_of(projection)
            if not alias:
                continue
            if _is_snake(alias):
                snake.append(alias)
            elif _is_camel_or_pascal(alias):
                camel.append(alias)

        if not snake or not camel:
            return

        dominant = ctx.params.get("dominant_casing", "auto")
        majority = "snake" if len(snake) >= len(camel) else "camel"
        if dominant in {"snake", "camel"}:
            majority = dominant
        minority = camel if majority == "snake" else snake
        if not minority:
            return

        yield self.make_finding(
            ctx,
            line=1,
            column=1,
            message=(
                f"Inconsistent casing: {len(snake)} snake_case + {len(camel)} camel/Pascal "
                f"columns. Rename {', '.join(sorted(minority)[:5])}"
                + (" …" if len(minority) > 5 else "")
                + f" to match `{majority}` convention."
            ),
            code_context=f"Q007:{majority}",
        )


def _first_outer_select(ast) -> exp.Select | None:
    for select in ast.find_all(exp.Select):
        parent = select.parent
        inside_cte = False
        while parent is not None:
            if isinstance(parent, exp.CTE):
                inside_cte = True
                break
            parent = parent.parent
        if not inside_cte:
            return select
    return None


def _alias_of(projection) -> str | None:
    if isinstance(projection, exp.Alias):
        return projection.alias_or_name
    if isinstance(projection, exp.Column):
        return projection.name
    return None


def _is_snake(name: str) -> bool:
    return name.islower() and ("_" in name or name.isalpha())


def _is_camel_or_pascal(name: str) -> bool:
    if "_" in name:
        return False
    return any(c.isupper() for c in name) and any(c.islower() for c in name)
