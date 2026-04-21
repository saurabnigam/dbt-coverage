"""SPEC-28 §2 — P006: fan-out join risk (join without GROUP BY / QUALIFY).

Heuristic: a LEFT / INNER join with an equality predicate on a non-unique
side can multiply rows. Because we can't guarantee uniqueness from SQL
alone, we flag *potential* fan-out: any join whose parent SELECT has
neither a ``GROUP BY`` nor a ``QUALIFY`` / ``DISTINCT`` projection, and
whose ON clause doesn't include a unique key marker (column name ending
in ``_id`` / ``_key``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_UNIQUE_SUFFIXES = ("_id", "_key", "id", "pk")


class P006FanOutJoinRule(BaseRule):
    id: ClassVar[str] = "P006"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "[fan-out risk] Join may multiply rows; verify uniqueness"
    confidence_base: ClassVar[float] = 0.6

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return
        for select in node.ast.find_all(exp.Select):
            if select.args.get("group") is not None or select.args.get("qualify") is not None:
                continue
            if _has_distinct(select):
                continue
            for join in select.args.get("joins") or []:
                on = join.args.get("on")
                if on is None:
                    continue
                if _on_has_unique_key(on):
                    continue
                line = node.line_map.get(_line_of(join) or 1, _line_of(join) or 1)
                yield self.make_finding(
                    ctx,
                    line=line,
                    column=1,
                    message=(
                        "[fan-out risk] Join on a non-unique key without "
                        "GROUP BY/QUALIFY — may multiply rows."
                    ),
                    code_context=f"P006:{join.sql()[:120]}",
                )


def _has_distinct(select: exp.Select) -> bool:
    # DISTINCT lives on the Select itself (args["distinct"]) in sqlglot.
    return select.args.get("distinct") is not None


def _on_has_unique_key(on) -> bool:
    for col in on.find_all(exp.Column):
        name = (col.name or "").lower()
        if any(name.endswith(sfx) for sfx in _UNIQUE_SUFFIXES):
            return True
    return False


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
