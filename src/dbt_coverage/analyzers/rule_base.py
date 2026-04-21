"""SPEC-07 §4.1 — Rule Protocol + RuleContext + BaseRule helper."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from dbt_coverage.core import (
    Category,
    ComplexityMetrics,
    Finding,
    FindingType,
    ParsedNode,
    Severity,
    TestResult,
    Tier,
    compute_fingerprint,
)

if TYPE_CHECKING:
    from dbt_coverage.graph import AnalysisGraph
    from dbt_coverage.scanners import ProjectIndex


@dataclass
class RuleContext:
    """Immutable view passed to every Rule.check() call."""

    node: ParsedNode | None
    node_id: str | None
    graph: AnalysisGraph
    project: ProjectIndex
    artifacts: Any | None = None  # phase-2
    params: dict[str, Any] = field(default_factory=dict)
    confidence_min: float = 0.0
    complexity: dict[str, ComplexityMetrics] = field(default_factory=dict)
    test_results: list[TestResult] = field(default_factory=list)
    # SPEC-32 §6 — detected dbt version from the DbtTestAdapter. Rules that
    # depend on dbt 1.8+ features (unit tests) can short-circuit on older projects.
    dbt_version: str | None = None


class Rule(Protocol):
    id: ClassVar[str]
    default_severity: ClassVar[Severity]
    default_tier: ClassVar[Tier]
    category: ClassVar[Category]
    finding_type: ClassVar[FindingType]
    description: ClassVar[str]
    confidence_base: ClassVar[float]
    applies_to_node: ClassVar[bool]
    requires_ast: ClassVar[bool]
    # SPEC-33 §4 — optional declarative prerequisites. Engine uses them to
    # emit typed ``CheckSkip`` entries *before* dispatching to ``check()``.
    required_render_mode: ClassVar[str | None]
    required_adapter: ClassVar[str | None]

    def check(self, ctx: RuleContext) -> Iterable[Finding]: ...


class BaseRule:
    """Convenience base class. Subclasses declare class-level attrs matching Rule protocol."""

    id: ClassVar[str] = ""
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_2_WARN
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = ""
    confidence_base: ClassVar[float] = 0.9
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True
    # SPEC-33 §4 — both ``None`` by default → no pre-dispatch gating.
    required_render_mode: ClassVar[str | None] = None
    required_adapter: ClassVar[str | None] = None

    def make_finding(
        self,
        ctx: RuleContext,
        line: int,
        column: int,
        message: str,
        *,
        code_context: str,
        end_line: int | None = None,
        end_column: int | None = None,
        confidence: float | None = None,
        severity_override: Severity | None = None,
        tier_override: Tier | None = None,
        file_path_override: Path | None = None,
    ) -> Finding:
        if ctx.node is not None:
            fp = file_path_override or ctx.node.file_path
        else:
            assert file_path_override is not None, (
                "file_path_override required for project-level rules"
            )
            fp = file_path_override

        # ``line``/``column`` are already source-line-resolved by the caller.
        line_i = max(1, int(line))
        col_i = max(1, int(column))
        conf = float(self.confidence_base if confidence is None else confidence)
        conf = max(0.0, min(1.0, conf))
        sev = severity_override or self.default_severity
        tier = tier_override or self.default_tier

        return Finding(
            rule_id=self.id,
            severity=sev,
            category=self.category,
            type=self.finding_type,
            tier=tier,
            confidence=conf,
            message=message,
            file_path=fp,
            line=line_i,
            column=col_i,
            end_line=end_line,
            end_column=end_column,
            node_id=ctx.node_id,
            fingerprint=compute_fingerprint(self.id, str(fp), code_context),
        )
