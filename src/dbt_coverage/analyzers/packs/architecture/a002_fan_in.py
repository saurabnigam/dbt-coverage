"""SPEC-27 §3 — A002: excessive upstream fan-in for a single model."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class A002FanInRule(BaseRule):
    id: ClassVar[str] = "A002"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.ARCHITECTURE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Model has too many upstream dependencies (fan-in)"
    confidence_base: ClassVar[float] = 0.9
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        threshold = int(ctx.params.get("threshold", 15))
        for nid, entry in ctx.project.models.items():
            parents = ctx.graph.get_upstream(nid)
            if len(parents) <= threshold:
                continue
            path = Path(entry.sql_file.path) if entry.sql_file else Path("")
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message=(
                    f"Fan-in: {len(parents)} upstream models. "
                    "Model is doing too much; consider splitting."
                ),
                code_context=f"A002:{nid}:{len(parents)}",
                file_path_override=path,
            )
