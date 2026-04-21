"""SPEC-32 §6.T001 — tests defined in manifest that never executed."""

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


class T001UnexecutedTestRule(BaseRule):
    """Flag every ``TestResult`` whose ``executed=False``.

    Prevents silent coverage gaps when CI runs ``dbt test --select`` partials or
    forgets to upload ``run_results.json``. Tier 1 / ERROR by default; the only
    escape hatches are ``dbtcov.yml`` overrides and running the full test suite.
    """

    id: ClassVar[str] = "T001"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.TESTING
    finding_type: ClassVar[FindingType] = FindingType.COVERAGE
    description: ClassVar[str] = "Test defined in manifest but not executed"
    confidence_base: ClassVar[float] = 1.0
    applies_to_node: ClassVar[bool] = False  # project-level: walks test_results
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        for tr in ctx.test_results:
            if tr.executed:
                continue

            fp = tr.file_path if tr.file_path is not None else Path("manifest.json")
            if fp.is_absolute():
                fp = Path(fp.name)

            kind_label = "unit test" if tr.kind is TestKind.UNIT else "test"
            message = (
                f"{kind_label.capitalize()} `{tr.test_name}` declared in manifest "
                "but did not execute. Attach run_results.json from a full "
                "`dbt test` run, or add to dbtcov.yml `overrides:`."
            )
            code_context = (
                f"{tr.test_kind}:{tr.test_name}@{tr.model_unique_id or 'unknown'}"
            )

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
