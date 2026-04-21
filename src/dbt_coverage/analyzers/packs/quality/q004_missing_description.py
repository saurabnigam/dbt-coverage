"""SPEC-29 §2 — Q004: model has no description in schema.yml.

Runs off the project index (no AST required), so it fires even on
``parse_failed`` models. We treat empty / whitespace-only descriptions as
missing too.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class Q004MissingDescriptionRule(BaseRule):
    id: ClassVar[str] = "Q004"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Model is missing a description in schema.yml"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        for nid, entry in ctx.project.models.items():
            yml = getattr(entry, "yml_meta", None)
            desc = (yml.description if yml else None) or ""
            if desc.strip():
                continue
            # Prefer the schema.yml line so reviewers see where to add the key.
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
                message=f"Model `{entry.name}` has no description in schema.yml.",
                code_context=f"Q004:{nid}",
                file_path_override=fp,
            )
