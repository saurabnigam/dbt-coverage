"""SPEC-01 §4.2–4.5 — Finding, CoverageMetric, ScanResult, RenderStats."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adapter_types import AdapterInvocation
from .complexity import ComplexityMetrics
from .enums import (
    Category,
    CheckSkipReason,
    FindingType,
    Severity,
    SuppressionSource,
    Tier,
)
from .test_result import TestResult

_APPROX_RATIO_DIMENSIONS = {"test_weighted_cc"}


class Suppression(BaseModel):
    """SPEC-31 §3 — reviewer-attested reason a finding is accepted/waived."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SuppressionSource
    reason: str
    reviewer: str | None = None
    expires: date | None = None
    entry_id: str | None = None


class Finding(BaseModel):
    """A single rule violation. Immutable; safe to hash via fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    severity: Severity
    category: Category
    type: FindingType
    tier: Tier
    confidence: float = Field(ge=0.0, le=1.0)
    message: str
    file_path: Path
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    end_line: int | None = None
    end_column: int | None = None
    node_id: str | None = None
    fingerprint: str
    is_new: bool = False
    fix_hint: str | None = None
    origins: list[str] = Field(default_factory=list)
    # SPEC-31 §3 — populated by WaiverResolver.
    suppressed: bool = False
    suppression: Suppression | None = None
    # SPEC-25 §4.8 — compiled-source provenance (None for MOCK rendered findings).
    compiled_path: Path | None = None

    @field_validator("file_path")
    @classmethod
    def _relative_path(cls, v: Path) -> Path:
        if v.is_absolute():
            raise ValueError(f"file_path must be relative to project_root, got {v}")
        return v

    @model_validator(mode="after")
    def _end_after_start(self) -> Finding:
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError(f"end_line {self.end_line} < line {self.line}")
        return self


class CoverageMetric(BaseModel):
    # Reporters/tests mutate ``notes`` after construction (e.g. to note
    # dbt_version_below_1_8), so this model is not frozen.
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "test",
        "doc",
        "unit",
        "column",
        "pii",
        "test_meaningful",
        "test_weighted_cc",
        "test_unit",
        "complexity",
    ]
    covered: int = Field(ge=0)
    total: int = Field(ge=0)
    ratio: float = Field(ge=0.0, le=1.0)
    per_node: dict[str, tuple[int, int]] = Field(default_factory=dict)
    # SPEC-32 §5 — free-form dimension notes (e.g. "dbt_version_below_1_8").
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_consistency(self) -> CoverageMetric:
        if self.total < self.covered:
            raise ValueError(f"total {self.total} < covered {self.covered}")
        if self.total > 0 and self.dimension not in _APPROX_RATIO_DIMENSIONS:
            expected = self.covered / self.total
            if abs(self.ratio - expected) > 1e-9:
                raise ValueError(f"ratio {self.ratio} != covered/total {expected}")
        return self


class RenderStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_files: int = Field(ge=0)
    rendered_mock: int = Field(default=0, ge=0)
    rendered_partial: int = Field(default=0, ge=0)
    # SPEC-25 §4.1 — "rendered_compiled" is the canonical name; "rendered_dbt"
    # retained as a read-only alias for any code that still dereferences it.
    rendered_compiled: int = Field(default=0, ge=0)
    render_uncertain: int = Field(default=0, ge=0)
    parse_success: int = Field(default=0, ge=0)
    parse_failed: int = Field(default=0, ge=0)

    @property
    def rendered_dbt(self) -> int:
        return self.rendered_compiled


class ModelSummary(BaseModel):
    """Per-model assessment row.  Included in ScanResult.model_summaries.

    Score (0–100) is a simple penalty model:
      -30  no test declared anywhere in schema.yml
      -20  doc coverage for this model < 50 %
      -30  any TIER_1_ENFORCED finding (excluding suppressed)
      -10  any TIER_2_WARN finding (excluding suppressed)
      -10  SQL parse failed (AST-level checks skipped)

    ``data_test_count`` / ``unit_test_count`` / ``tests_not_run_count`` are
    populated per SPEC-32 §3. ``waived_count`` + ``skip_count`` per SPEC-31
    / SPEC-33.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    name: str
    file_path: str
    parse_success: bool = True
    render_uncertain: bool = False
    test_covered: bool = False
    doc_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    tier1_rules: list[str] = Field(default_factory=list)
    tier2_rules: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=100, default=100)
    data_test_count: int = 0
    unit_test_count: int = 0
    tests_not_run_count: int = 0
    waived_count: int = 0
    skip_count: int = 0


class CheckSkip(BaseModel):
    """SPEC-33 §3 — single (rule, node) pair that did not execute."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    node_id: str | None = None
    reason: CheckSkipReason
    details: str | None = None


class AggregatedCheckSkip(BaseModel):
    """SPEC-33 §3 — per-(rule, reason) rollup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    reason: CheckSkipReason
    count: int = Field(ge=0)
    affected_node_ids: list[str] = Field(default_factory=list)
    sample_details: str | None = None


class CheckSkipSummary(BaseModel):
    """SPEC-33 §3 — overall skip statistics. Always emitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_skips: int = Field(default=0, ge=0)
    attempted_checks: int = Field(default=0, ge=0)
    effective_coverage_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    by_reason: dict[CheckSkipReason, int] = Field(default_factory=dict)
    by_rule: dict[str, int] = Field(default_factory=dict)
    affected_nodes: int = Field(default=0, ge=0)


class ScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)
    coverage: list[CoverageMetric] = Field(default_factory=list)
    model_summaries: list[ModelSummary] = Field(default_factory=list)
    project_root: Path
    project_name: str | None = None
    dbt_version_detected: str | None = None
    dialect: str
    render_stats: RenderStats
    scan_duration_ms: int = Field(default=0, ge=0)
    schema_version: int = 1
    complexity: dict[str, ComplexityMetrics] = Field(default_factory=dict)
    test_results: list[TestResult] = Field(default_factory=list)
    adapter_invocations: list[AdapterInvocation] = Field(default_factory=list)
    # SPEC-33 §3 — skip tracking. ``check_skip_summary`` is always populated.
    check_skip_summary: CheckSkipSummary = Field(default_factory=CheckSkipSummary)
    check_skips_aggregated: list[AggregatedCheckSkip] = Field(default_factory=list)
    check_skips: list[CheckSkip] = Field(default_factory=list)
