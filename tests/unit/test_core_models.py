"""Unit tests for core domain model."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_coverage.core import (
    Category,
    CoverageMetric,
    Finding,
    FindingType,
    RenderStats,
    ScanResult,
    Severity,
    Tier,
    compute_fingerprint,
)


def _make_finding(**overrides) -> Finding:
    base = dict(
        rule_id="Q001",
        severity=Severity.MAJOR,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=Tier.TIER_2_WARN,
        confidence=0.9,
        message="hello",
        file_path=Path("models/stg.sql"),
        line=1,
        column=1,
        fingerprint="deadbeef" * 8,
    )
    base.update(overrides)
    return Finding(**base)


def test_finding_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        _make_finding(file_path=Path("/abs/path/stg.sql"))


def test_finding_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _make_finding(line=5, end_line=3)


def test_coverage_metric_consistency() -> None:
    m = CoverageMetric(dimension="test", covered=2, total=4, ratio=0.5)
    assert m.ratio == 0.5


def test_coverage_metric_rejects_bad_ratio() -> None:
    with pytest.raises(ValidationError):
        CoverageMetric(dimension="test", covered=2, total=4, ratio=0.7)


def test_fingerprint_is_stable_and_different_for_different_inputs() -> None:
    a = compute_fingerprint("Q001", "models/a.sql", "snippet")
    b = compute_fingerprint("Q001", "models/a.sql", "snippet")
    c = compute_fingerprint("Q001", "models/b.sql", "snippet")
    assert a == b
    assert a != c


def test_scanresult_roundtrip(tmp_path: Path) -> None:
    rs = RenderStats(total_files=0)
    sr = ScanResult(
        project_root=tmp_path,
        dialect="postgres",
        render_stats=rs,
    )
    payload = sr.model_dump_json()
    reloaded = ScanResult.model_validate_json(payload)
    assert reloaded.dialect == "postgres"
