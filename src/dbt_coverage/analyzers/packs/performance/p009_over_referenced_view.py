"""SPEC-28 §2 — P009: over-referenced view.

A model materialised as ``view`` whose in-degree (downstream referring
models) exceeds ``threshold`` gets recomputed N times by the warehouse
because views are inlined at each reference site. Promoting to ``table``
or ``ephemeral`` + inlining is usually cheaper.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_CONFIG_RE = re.compile(r"materialized\s*=\s*['\"](?P<mat>[a-z_]+)['\"]", re.I)


class P009OverReferencedViewRule(BaseRule):
    id: ClassVar[str] = "P009"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "[recomputed N×] View referenced by too many downstream models"
    confidence_base: ClassVar[float] = 0.85
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        threshold = int(ctx.params.get("threshold", 5))
        for nid, entry in ctx.project.models.items():
            mat = _materialization(entry)
            if mat != "view":
                continue
            downstream = ctx.graph.get_downstream(nid)
            if len(downstream) <= threshold:
                continue
            path = Path(entry.sql_file.path) if entry.sql_file else Path("")
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message=(
                    f"[recomputed N×] View `{entry.name}` is referenced by {len(downstream)} "
                    f"downstream models (> {threshold}); consider table/incremental."
                ),
                code_context=f"P009:{nid}",
                file_path_override=path,
            )


def _materialization(entry) -> str | None:
    yml = getattr(entry, "yml_meta", None)
    if yml is not None:
        mat = (yml.config or {}).get("materialized")
        if mat:
            return str(mat).lower()
    sql = getattr(entry.sql_file, "content", "") if entry.sql_file else ""
    m = _CONFIG_RE.search(sql or "")
    return m.group("mat").lower() if m else None
