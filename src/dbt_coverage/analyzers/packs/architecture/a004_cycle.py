"""SPEC-27 §3 — A004: circular dependency in the DAG.

Delegates to ``DAG.detect_cycles`` and emits one finding per cycle. Fatal
tier; the downstream compiler will refuse to execute this project anyway.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class A004CircularDepRule(BaseRule):
    id: ClassVar[str] = "A004"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.ARCHITECTURE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "Circular dependency in model DAG"
    confidence_base: ClassVar[float] = 1.0
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        dag = ctx.graph.dag
        try:
            cycles = dag.detect_cycles()
        except Exception:
            return
        for cycle in cycles:
            if not cycle:
                continue
            path = _first_model_path(cycle, ctx.project)
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message="Circular dependency: " + " → ".join(cycle + [cycle[0]]),
                code_context="A004:" + "->".join(cycle),
                file_path_override=path,
            )


def _first_model_path(cycle: list[str], project) -> Path:
    for nid in cycle:
        entry = project.models.get(nid)
        if entry and entry.sql_file:
            return Path(entry.sql_file.path)
    return Path("project")
