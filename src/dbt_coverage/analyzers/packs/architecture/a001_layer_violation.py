"""SPEC-27 §3 — A001: DAG edge violates ``architecture.allowed_edges``."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier
from dbt_coverage.graph.layers import classify_layer, edge_is_allowed


class A001LayerViolationRule(BaseRule):
    id: ClassVar[str] = "A001"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.ARCHITECTURE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "DAG edge violates allowed layer transitions"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        arch = ctx.params.get("_architecture")
        if arch is None:
            return

        project = ctx.project
        graph = ctx.graph
        for from_id in project.models:
            from_entry = project.models[from_id]
            from_layer = classify_layer(
                from_id, from_entry.sql_file.path if from_entry.sql_file else Path(""), arch
            )
            for to_id in graph.get_downstream(from_id):
                to_entry = project.models.get(to_id)
                if to_entry is None:
                    continue
                to_layer = classify_layer(
                    to_id,
                    to_entry.sql_file.path if to_entry.sql_file else Path(""),
                    arch,
                )
                if edge_is_allowed(from_layer, to_layer, arch):
                    continue
                yield self.make_finding(
                    ctx,
                    line=1,
                    column=1,
                    message=(
                        f"Layer violation: `{from_entry.name}` ({from_layer}) → "
                        f"`{to_entry.name}` ({to_layer}) is not in allowed_edges."
                    ),
                    code_context=f"A001:{from_id}->{to_id}",
                    file_path_override=Path(to_entry.sql_file.path),
                )
