"""SPEC-32 §6.T003 — malformed unit test (missing given / expect / empty rows)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import (
    Category,
    Finding,
    FindingType,
    Severity,
    Tier,
    TestKind,
    compute_fingerprint,
)


class T003MalformedUnitTestRule(BaseRule):
    """Fires when a ``unit_tests:`` entry would silently pass because its
    fixtures are missing or empty.

    Detected at adapter parse time and plumbed through via
    ``TestResult.malformed_reason``. The rule just surfaces the reason on
    the correct file/line.
    """

    id: ClassVar[str] = "T003"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.TESTING
    finding_type: ClassVar[FindingType] = FindingType.BUG
    description: ClassVar[str] = "Malformed unit test (missing given/expect)"
    confidence_base: ClassVar[float] = 1.0
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        for tr in ctx.test_results:
            if tr.kind is not TestKind.UNIT:
                continue
            if not tr.malformed_reason:
                continue

            fp = tr.file_path if tr.file_path is not None else Path("manifest.json")
            if fp.is_absolute():
                fp = Path(fp.name)

            message = (
                f"Unit test `{tr.test_name}` is malformed: {tr.malformed_reason}. "
                "The test would silently pass — add proper `given` rows and "
                "`expect.rows` fixtures."
            )
            code_context = f"malformed_unit:{tr.test_name}:{tr.malformed_reason}"

            yield Finding(
                rule_id=self.id,
                severity=self.default_severity,
                category=self.category,
                type=self.finding_type,
                tier=self.default_tier,
                confidence=self.confidence_base,
                message=message,
                file_path=fp,
                line=1,
                column=1,
                node_id=tr.model_unique_id,
                fingerprint=compute_fingerprint(self.id, str(fp), code_context),
            )
