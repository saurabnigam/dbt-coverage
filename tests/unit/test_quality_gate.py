"""Unit tests for the quality gate."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.core import (
    Category,
    CoverageMetric,
    Finding,
    FindingType,
    RenderStats,
    ScanResult,
    Severity,
    Tier,
)
from dbt_coverage.quality_gates import CoverageThreshold, GateConfig, evaluate


def _sr(
    findings: list[Finding] | None = None,
    coverage: list[CoverageMetric] | None = None,
) -> ScanResult:
    return ScanResult(
        findings=findings or [],
        coverage=coverage or [],
        project_root=Path("/tmp"),
        dialect="postgres",
        render_stats=RenderStats(total_files=1, parse_success=1),
    )


def _f(rule_id: str, tier: Tier, is_new: bool = False) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=Severity.MAJOR,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=tier,
        confidence=0.9,
        message="m",
        file_path=Path("a.sql"),
        line=1,
        column=1,
        fingerprint="a" * 16,
        is_new=is_new,
    )


def test_gate_passes_when_no_findings() -> None:
    cfg = GateConfig()
    r = evaluate(_sr(), cfg)
    assert r.passed


def test_gate_fails_on_tier1_finding() -> None:
    cfg = GateConfig(fail_on_tier=Tier.TIER_1_ENFORCED)
    r = evaluate(_sr([_f("Q001", Tier.TIER_1_ENFORCED)]), cfg)
    assert not r.passed
    assert r.counted_findings == 1


def test_gate_passes_tier2_when_fail_on_tier1_only() -> None:
    cfg = GateConfig(fail_on_tier=Tier.TIER_1_ENFORCED)
    r = evaluate(_sr([_f("R001", Tier.TIER_2_WARN)]), cfg)
    assert r.passed


def test_fail_on_new_only_suppresses_old_findings() -> None:
    cfg = GateConfig(fail_on_tier=Tier.TIER_1_ENFORCED, fail_on_new_only=True)
    old = _f("Q001", Tier.TIER_1_ENFORCED, is_new=False)
    new = _f("Q002", Tier.TIER_1_ENFORCED, is_new=True)
    r = evaluate(_sr([old, new]), cfg)
    assert not r.passed
    assert r.counted_findings == 1
    assert r.suppressed_findings == 1


def test_coverage_regression_fails_gate() -> None:
    cfg = GateConfig(
        fail_on_tier=Tier.TIER_1_ENFORCED,
        coverage={"test": CoverageThreshold(min=0.8)},
    )
    cov = [CoverageMetric(dimension="test", covered=2, total=10, ratio=0.2)]
    r = evaluate(_sr(coverage=cov), cfg)
    assert not r.passed
    assert any(reason.code == "COVERAGE_BELOW_MIN" for reason in r.reasons)


def test_coverage_regression_passes_when_above_min() -> None:
    cfg = GateConfig(
        fail_on_tier=Tier.TIER_1_ENFORCED,
        coverage={"test": CoverageThreshold(min=0.5)},
    )
    cov = [CoverageMetric(dimension="test", covered=8, total=10, ratio=0.8)]
    r = evaluate(_sr(coverage=cov), cfg)
    assert r.passed
