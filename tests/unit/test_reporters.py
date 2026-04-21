"""Unit tests for JSON/SARIF reporters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from dbt_coverage.reporters import JSONReporter, SARIFReporter


@pytest.fixture()
def sample_result(tmp_path: Path) -> ScanResult:
    f = Finding(
        rule_id="Q001",
        severity=Severity.MAJOR,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=Tier.TIER_1_ENFORCED,
        confidence=0.9,
        message="Avoid SELECT *",
        file_path=Path("models/stg.sql"),
        line=3,
        column=1,
        fingerprint="abc1234567890def",
    )
    return ScanResult(
        findings=[f],
        coverage=[CoverageMetric(dimension="test", covered=1, total=2, ratio=0.5)],
        project_root=tmp_path,
        project_name="proj",
        dialect="postgres",
        render_stats=RenderStats(total_files=1, parse_success=1),
    )


def test_json_reporter_writes_file(sample_result, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    JSONReporter().emit(sample_result, out_dir)
    target = out_dir / "findings.json"
    assert target.exists()
    doc = json.loads(target.read_text())
    assert doc["findings"][0]["rule_id"] == "Q001"


def test_sarif_reporter_writes_valid_shape(sample_result, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    SARIFReporter().emit(sample_result, out_dir)
    target = out_dir / "findings.sarif"
    assert target.exists()
    doc = json.loads(target.read_text())
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "dbtcov"
    result = run["results"][0]
    assert result["ruleId"] == "Q001"
    assert result["level"] == "warning"
    assert result["partialFingerprints"]["dbtcov/v1"] == "abc1234567890def"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ] == "models/stg.sql"
    assert (
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uriBaseId"]
        == "%SRCROOT%"
    )
