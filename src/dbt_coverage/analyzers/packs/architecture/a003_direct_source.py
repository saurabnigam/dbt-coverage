"""SPEC-27 §3 — A003: non-staging model reads a source directly.

Staging is the boundary layer. Any intermediate/mart that holds a reference
to ``source(...)`` (detected via the node's ``sources`` list) is a leaky
abstraction that couples downstream to raw schemas.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier
from dbt_coverage.graph.layers import classify_layer


class A003DirectSourceBypassRule(BaseRule):
    id: ClassVar[str] = "A003"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.ARCHITECTURE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "Non-staging model reads raw sources directly"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = False

    # Layers *below* which (exclusive) source reads are still acceptable.
    # "source" and "staging" are always allowed — anything else trips this rule.
    _ALLOWED_LAYERS = {"source", "staging", None}

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or not node.sources:
            return
        arch = ctx.params.get("_architecture")
        if arch is None:
            return
        layer = classify_layer(
            ctx.node_id or "",
            node.file_path,
            arch,
        )
        if layer in self._ALLOWED_LAYERS:
            return
        entry = ctx.project.models.get(ctx.node_id or "")
        name = entry.name if entry else (ctx.node_id or "").split(".")[-1]
        yield self.make_finding(
            ctx,
            line=1,
            column=1,
            message=(
                f"{layer} model `{name}` reads sources directly; route through staging."
            ),
            code_context=f"A003:{ctx.node_id}",
            file_path_override=Path(node.file_path),
        )
