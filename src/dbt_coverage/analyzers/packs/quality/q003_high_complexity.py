"""SPEC-20 — Q003 High Cyclomatic Complexity.

Reads the precomputed ``ctx.complexity[node_id]`` map (SPEC-19) and
emits a finding when CC crosses configurable warn/block thresholds.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from typing import Any

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, ComplexityMetrics, Finding, FindingType, Severity, Tier


class Q003HighComplexityRule(BaseRule):
    id = "Q003"
    default_severity = Severity.MAJOR
    default_tier = Tier.TIER_2_WARN
    category = Category.QUALITY
    finding_type = FindingType.CODE_SMELL
    description = "SQL model cyclomatic complexity exceeds threshold"
    confidence_base = 0.95
    applies_to_node = True
    requires_ast = False

    _DEFAULT_WARN = 15
    _DEFAULT_BLOCK = 30

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        if ctx.node is None or ctx.node_id is None:
            return
        metrics = ctx.complexity.get(ctx.node_id)
        if metrics is None:
            return

        params = self._params(ctx.params)
        cc = metrics.cc
        if not params["include_jinja"]:
            cc = cc - metrics.jinja_ifs - metrics.jinja_fors
        if cc < 1:
            cc = 1

        if self._is_exempt(ctx, params["exempt_models"]):
            return

        if cc >= params["threshold_block"]:
            yield self._make(
                ctx, metrics, cc, params["threshold_block"],
                severity=Severity.CRITICAL,
                tier=Tier.TIER_1_ENFORCED,
                level="block",
            )
        elif cc >= params["threshold_warn"]:
            yield self._make(
                ctx, metrics, cc, params["threshold_warn"],
                severity=Severity.MAJOR,
                tier=Tier.TIER_2_WARN,
                level="warn",
            )

    def _params(self, raw: dict[str, Any]) -> dict[str, Any]:
        warn = int(raw.get("threshold_warn", self._DEFAULT_WARN))
        block = int(raw.get("threshold_block", self._DEFAULT_BLOCK))
        if block < warn:
            # Config validation should catch this earlier; be defensive.
            block = warn
        return {
            "threshold_warn": warn,
            "threshold_block": block,
            "include_jinja": bool(raw.get("include_jinja", True)),
            "exempt_models": list(raw.get("exempt_models") or []),
        }

    def _make(
        self,
        ctx: RuleContext,
        metrics: ComplexityMetrics,
        cc: int,
        threshold: int,
        severity: Severity,
        tier: Tier,
        level: str,
    ) -> Finding:
        top = self._top_contributors(metrics, k=3)
        msg = (
            f"Cyclomatic complexity {cc} >= {level} threshold {threshold}. "
            f"Top contributors: {top}."
        )
        return self.make_finding(
            ctx,
            line=1,
            column=1,
            message=msg,
            code_context=f"Q003:{cc}",
            confidence=0.95,
            severity_override=severity,
            tier_override=tier,
        )

    @staticmethod
    def _top_contributors(metrics: ComplexityMetrics, k: int = 3) -> str:
        parts = [
            ("joins", metrics.join_count),
            ("CASE arms", metrics.case_arms),
            ("AND/OR", metrics.boolean_ops),
            ("UNION arms", metrics.set_op_arms),
            ("IF/IIF", metrics.iff_count),
            ("correlated subqueries", metrics.subqueries),
            ("{% if %}", metrics.jinja_ifs),
            ("{% for %}", metrics.jinja_fors),
        ]
        ordered = sorted(parts, key=lambda kv: kv[1], reverse=True)
        nz = [f"{name}={n}" for name, n in ordered if n > 0][:k]
        return ", ".join(nz) if nz else "none"

    @staticmethod
    def _is_exempt(ctx: RuleContext, patterns: list[str]) -> bool:
        if not patterns:
            return False
        if ctx.node is None:
            return False
        s = ctx.node_id or str(ctx.node.file_path)
        return any(fnmatch(s, p) for p in patterns)
