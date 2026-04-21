"""SPEC-26 §2 — R005: identical projection expression across ≥ N models.

Project-level rule. Walks every parsed node's outermost SELECT, fingerprints
each projection with ``sqlglot.sql(pretty=False)`` (after trimming aliases)
and clusters identical expressions. When a cluster spans ``min_occurrences``
or more distinct models the rule emits a single finding on the *first*
occurrence, listing the others.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class R005DuplicateExpressionRule(BaseRule):
    id: ClassVar[str] = "R005"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = (
        "Projection expression duplicated across multiple models — extract to a macro"
    )
    confidence_base: ClassVar[float] = 0.75
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        min_occ = int(ctx.params.get("min_occurrences", 3))
        min_len = int(ctx.params.get("min_expr_length", 40))

        buckets: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        # bucket key → list of (node_id, expr_preview, line)
        for nid, entry in ctx.project.models.items():
            cast = ctx.graph.canonical_ast(nid)
            if cast is None:
                continue
            top_selects = list(cast.find_all(exp.Select))
            if not top_selects:
                continue
            for proj in top_selects[0].expressions or []:
                text = _expr_text(proj)
                if not text or len(text) < min_len:
                    continue
                key = hashlib.md5(text.encode("utf-8")).hexdigest()
                line = _line_of(proj) or 1
                buckets[key].append((nid, text, line))

        for key, entries in buckets.items():
            unique_nids = {nid for nid, _, _ in entries}
            if len(unique_nids) < min_occ:
                continue
            # First model alphabetically anchors the finding.
            anchor_nid = sorted(unique_nids)[0]
            others = sorted(unique_nids - {anchor_nid})
            anchor = next(e for e in entries if e[0] == anchor_nid)
            anchor_entry = ctx.project.models[anchor_nid]

            yield self.make_finding(
                ctx,
                line=anchor[2],
                column=1,
                message=(
                    f"Expression `{_truncate(anchor[1], 80)}` appears in "
                    f"{len(unique_nids)} models: {', '.join(others[:3])}"
                    f"{'…' if len(others) > 3 else ''}. Extract to a macro."
                ),
                code_context=f"R005:{key}",
                file_path_override=Path(anchor_entry.sql_file.path),
            )


def _expr_text(proj) -> str:
    # Strip trailing aliases so ``x::date as d`` and ``x::date as d2`` collapse.
    try:
        if isinstance(proj, exp.Alias):
            inner = proj.this
            return inner.sql(pretty=False).strip() if inner is not None else ""
        return proj.sql(pretty=False).strip()
    except Exception:
        return ""


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
