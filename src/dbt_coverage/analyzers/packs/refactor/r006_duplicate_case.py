"""SPEC-26 §2 — R006: identical ``CASE`` ladder across ≥ N models.

Same algorithm as R005 but scoped to ``exp.Case`` subtrees with ≥
``min_arms`` WHEN branches. Trivial two-arm CASEs (``CASE WHEN x THEN y END``)
are ignored.
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


class R006DuplicateCaseRule(BaseRule):
    id: ClassVar[str] = "R006"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = (
        "Identical CASE ladder across multiple models — extract to a macro"
    )
    confidence_base: ClassVar[float] = 0.75
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        min_occ = int(ctx.params.get("min_occurrences", 3))
        min_arms = int(ctx.params.get("min_arms", 3))

        buckets: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for nid, entry in ctx.project.models.items():
            cast = ctx.graph.canonical_ast(nid)
            if cast is None:
                continue
            for case in cast.find_all(exp.Case):
                arms = case.args.get("ifs") or []
                if len(arms) < min_arms:
                    continue
                text = _case_text(case)
                if not text:
                    continue
                key = hashlib.md5(text.encode("utf-8")).hexdigest()
                line = _line_of(case) or 1
                buckets[key].append((nid, text, line))

        for key, entries in buckets.items():
            unique_nids = {nid for nid, _, _ in entries}
            if len(unique_nids) < min_occ:
                continue
            anchor_nid = sorted(unique_nids)[0]
            others = sorted(unique_nids - {anchor_nid})
            anchor = next(e for e in entries if e[0] == anchor_nid)
            anchor_entry = ctx.project.models[anchor_nid]

            yield self.make_finding(
                ctx,
                line=anchor[2],
                column=1,
                message=(
                    f"CASE ladder duplicated across {len(unique_nids)} models: "
                    f"{', '.join(others[:3])}{'…' if len(others) > 3 else ''}. "
                    "Extract to a macro."
                ),
                code_context=f"R006:{key}",
                file_path_override=Path(anchor_entry.sql_file.path),
            )


def _case_text(case: exp.Case) -> str:
    try:
        return case.sql(pretty=False).strip()
    except Exception:
        return ""


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
