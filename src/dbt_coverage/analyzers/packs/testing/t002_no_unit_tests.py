"""SPEC-32 §6.T002 — model has no unit tests."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import (
    Category,
    Finding,
    FindingType,
    Severity,
    Tier,
    TestKind,
)


class T002NoUnitTestsRule(BaseRule):
    """Fires once per model that has zero ``TestKind.UNIT`` tests attached.

    Auto-suppressed on:
      - dbt < 1.8 projects (no ``unit_tests:`` support),
      - models whose path matches any ``testing.unit_tests.exempt`` glob,
      - models classified as staging (``stg_*``), seeds, sources, or snapshots.
    """

    id: ClassVar[str] = "T002"
    default_severity: ClassVar[Severity] = Severity.MINOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.TESTING
    finding_type: ClassVar[FindingType] = FindingType.COVERAGE
    description: ClassVar[str] = "Model has no unit tests"
    confidence_base: ClassVar[float] = 0.9
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or ctx.node_id is None:
            return

        if not ctx.node_id.startswith("model."):
            return

        if _below_1_8(ctx.dbt_version):
            # Skip quietly; test_unit dimension still shows 0/N with a note.
            return

        # Ignore staging layer by naming convention — unit tests shine on
        # logic-bearing marts / intermediates.
        if node.file_path and "staging" in node.file_path.parts:
            return

        exempt_globs: list[str] = list(ctx.params.get("exempt", []) or [])
        if node.file_path and any(
            fnmatch.fnmatch(str(node.file_path), g) for g in exempt_globs
        ):
            return

        has_unit = any(
            tr.kind is TestKind.UNIT and tr.model_unique_id == ctx.node_id
            for tr in ctx.test_results
        )
        if has_unit:
            return

        yield self.make_finding(
            ctx,
            line=1,
            column=1,
            message=(
                f"Model `{node.node_id or ctx.node_id}` has no unit tests. "
                "Consider adding a `unit_tests:` block with `given`/`expect`."
            ),
            code_context=f"no_unit_test:{ctx.node_id}",
        )


def _below_1_8(version: str | None) -> bool:
    if not version:
        return False
    parts = version.split(".")
    try:
        return (int(parts[0]), int(parts[1])) < (1, 8)
    except (ValueError, IndexError):
        return False
