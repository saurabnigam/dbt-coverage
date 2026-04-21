"""SPEC-27 §3 — A005: staging model exposes raw column names.

Heuristic: staging is expected to rename columns to project-standard
snake_case. A projection that is all-upper (``USER_ID``) or MixedCase
(``userId``) is a smell — the raw source column is leaking through.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier
from dbt_coverage.graph.layers import classify_layer


class A005LeakyAbstractionRule(BaseRule):
    id: ClassVar[str] = "A005"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.ARCHITECTURE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Staging model exposes a non-snake_case column"
    confidence_base: ClassVar[float] = 0.7
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        arch = ctx.params.get("_architecture")
        if arch is None:
            return
        layer = classify_layer(ctx.node_id or "", node.file_path, arch)
        if layer != "staging":
            return

        top_selects = list(node.ast.find_all(exp.Select))
        if not top_selects:
            return

        seen: set[str] = set()
        for proj in top_selects[0].expressions or []:
            name = _projection_alias(proj)
            if not name or name in seen:
                continue
            seen.add(name)
            if not _is_snake_case(name):
                line_rendered = _line_of(proj) or 1
                line_source = node.line_map.get(line_rendered, line_rendered)
                yield self.make_finding(
                    ctx,
                    line=line_source,
                    column=1,
                    message=f"Staging model exposes raw column `{name}`; rename to snake_case.",
                    code_context=f"A005:{name}",
                )


def _projection_alias(proj) -> str | None:
    if isinstance(proj, exp.Alias):
        try:
            return proj.alias
        except Exception:
            return None
    if isinstance(proj, exp.Column):
        try:
            return proj.name
        except Exception:
            return None
    return None


def _is_snake_case(name: str) -> bool:
    # snake_case: all lowercase letters + digits + underscores, no uppercase.
    return name == name.lower() and "_" in name or name.islower()


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
