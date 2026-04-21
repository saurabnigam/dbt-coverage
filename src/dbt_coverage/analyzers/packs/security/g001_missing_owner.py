"""SPEC-30 §3 — G001: model lacks an owner / team tag in schema.yml."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class G001MissingOwnerRule(BaseRule):
    id: ClassVar[str] = "G001"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.GOVERNANCE
    finding_type: ClassVar[FindingType] = FindingType.GOVERNANCE
    description: ClassVar[str] = "Model is missing meta.owner / meta.team"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        for nid, entry in ctx.project.models.items():
            yml = getattr(entry, "yml_meta", None)
            meta = (yml.meta if yml else {}) or {}
            if meta.get("owner") or meta.get("team"):
                continue
            if yml is not None:
                fp = Path(yml.file_path)
                line = max(1, int(getattr(yml, "line", 1)))
            elif entry.sql_file is not None:
                fp = Path(entry.sql_file.path)
                line = 1
            else:
                continue
            yield self.make_finding(
                ctx,
                line=line,
                column=1,
                message=(
                    f"Model `{entry.name}` has no `meta.owner` or `meta.team` — "
                    "add ownership info for accountability."
                ),
                code_context=f"G001:{nid}",
                file_path_override=fp,
            )
