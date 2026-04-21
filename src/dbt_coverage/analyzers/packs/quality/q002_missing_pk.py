"""SPEC-08a — Q002: Missing primary-key test (unique + not_null)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier


class Q002MissingPkRule(BaseRule):
    id: ClassVar[str] = "Q002"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "Model missing primary-key test (unique + not_null)"
    confidence_base: ClassVar[float] = 1.0
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = False  # YAML-only

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        if ctx.node is None or ctx.node_id is None:
            return
        entry = ctx.project.models.get(ctx.node_id)
        if entry is None or entry.yml_meta is None:
            return
        yml = entry.yml_meta

        pk_candidates = self._infer_pk(yml)
        if not pk_candidates:
            yield self.make_finding(
                ctx,
                line=yml.line,
                column=1,
                message=(
                    f"Model '{yml.name}' has no inferrable primary key. "
                    f"Declare `meta: {{primary_key: <col>}}` in schema.yml to unlock Q002."
                ),
                code_context=f"model:{yml.name}:no-pk",
                confidence=0.7,
                severity_override=Severity.MINOR,
                tier_override=Tier.TIER_2_WARN,
            )
            return

        col_by_name = {c.name: c for c in yml.columns}
        for col_name in pk_candidates:
            col = col_by_name.get(col_name)
            if col is None:
                yield self.make_finding(
                    ctx,
                    line=yml.line,
                    column=1,
                    message=(
                        f"Inferred PK column '{col_name}' in model '{yml.name}' "
                        f"is not listed in schema.yml"
                    ),
                    code_context=f"model:{yml.name}:pk:{col_name}:missing",
                    confidence=0.8,
                )
                continue
            has_unique = any(self._is_test(t, "unique") for t in col.tests)
            has_not_null = any(self._is_test(t, "not_null") for t in col.tests)
            if not (has_unique and has_not_null):
                missing = [
                    name
                    for ok, name in ((has_unique, "unique"), (has_not_null, "not_null"))
                    if not ok
                ]
                yield self.make_finding(
                    ctx,
                    line=yml.line,
                    column=1,
                    message=(
                        f"PK column '{col_name}' in model '{yml.name}' is missing "
                        f"{' + '.join(missing)} test(s)"
                    ),
                    code_context=f"model:{yml.name}:pk:{col_name}",
                )

    # -------------------------------------------------------------- helpers

    def _infer_pk(self, yml: Any) -> list[str]:
        pk = yml.meta.get("primary_key")
        if isinstance(pk, str):
            return [pk]
        if isinstance(pk, list):
            return [str(x) for x in pk]
        col_names = {c.name for c in yml.columns}
        for candidate in ("id", f"{yml.name}_id"):
            if candidate in col_names:
                return [candidate]
        return []

    @staticmethod
    def _is_test(test_entry: Any, name: str) -> bool:
        if isinstance(test_entry, str):
            return test_entry == name
        if isinstance(test_entry, dict):
            # {"unique": {"config": {...}}}
            if name in test_entry:
                return True
            if test_entry.get("name") == name:
                return True
        return False
