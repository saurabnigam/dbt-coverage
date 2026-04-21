"""SPEC-28 §2 — P010: incremental model without ``unique_key``.

Incremental materialisations that don't declare both ``unique_key`` and an
``incremental_strategy`` (``merge`` / ``delete+insert``) produce duplicates
on every backfill. This is a correctness bug masquerading as a perf issue.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_KEY_RE = re.compile(r"unique_key\s*=\s*['\"]?([A-Za-z0-9_\[\]\,\s'\"]+)", re.I)
_STRATEGY_RE = re.compile(r"incremental_strategy\s*=\s*['\"](?P<s>[a-z_+]+)['\"]", re.I)
_MAT_RE = re.compile(r"materialized\s*=\s*['\"](?P<mat>[a-z_]+)['\"]", re.I)


class P010IncrementalMissingKeyRule(BaseRule):
    id: ClassVar[str] = "P010"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.PERFORMANCE
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "[duplicates] Incremental model without unique_key/strategy"
    confidence_base: ClassVar[float] = 0.9
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        for nid, entry in ctx.project.models.items():
            mat, unique_key, strategy = _parse_config(entry)
            if mat != "incremental":
                continue
            if unique_key and strategy:
                continue
            missing: list[str] = []
            if not unique_key:
                missing.append("unique_key")
            if not strategy:
                missing.append("incremental_strategy")
            path = Path(entry.sql_file.path) if entry.sql_file else Path("")
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message=(
                    f"[duplicates] Incremental model `{entry.name}` is missing "
                    f"{' + '.join(missing)}; will duplicate rows on run."
                ),
                code_context=f"P010:{nid}",
                file_path_override=path,
            )


def _parse_config(entry) -> tuple[str | None, str | None, str | None]:
    mat: str | None = None
    unique_key: str | None = None
    strategy: str | None = None
    yml = getattr(entry, "yml_meta", None)
    if yml is not None:
        cfg = yml.config or {}
        mat = (cfg.get("materialized") or "").lower() or None
        unique_key = cfg.get("unique_key") or None
        strategy = (cfg.get("incremental_strategy") or "").lower() or None

    sql = getattr(entry.sql_file, "content", "") if entry.sql_file else ""
    if sql:
        if mat is None:
            m = _MAT_RE.search(sql)
            mat = m.group("mat").lower() if m else mat
        if unique_key is None:
            m = _KEY_RE.search(sql)
            unique_key = m.group(1).strip().strip("'\"") if m else unique_key
        if strategy is None:
            m = _STRATEGY_RE.search(sql)
            strategy = m.group("s").lower() if m else strategy
    return mat, unique_key, strategy
