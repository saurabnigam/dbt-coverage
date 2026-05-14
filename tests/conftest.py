"""Shared pytest fixtures — SPEC-13."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_dbt_project"
_QUALITY_SCORE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "quality_score_scenarios"


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    """Copy the packaged sample dbt project into an isolated tmp dir."""
    dst = tmp_path / "sample_dbt_project"
    shutil.copytree(_FIXTURE_DIR, dst)
    return dst


@pytest.fixture()
def scan_result(sample_project: Path):
    """Run the orchestrator against the sample project."""
    from dbt_coverage.cli.orchestrator import scan

    bundle = scan(sample_project)
    return bundle.result


@pytest.fixture(scope="session")
def quality_score_bundle():
    """Run the orchestrator once against the quality_score_scenarios fixture project.

    Scope is ``session`` so the scan (SQL parse + rule engine + coverage) runs
    exactly once for the entire test session, regardless of how many test modules
    consume this fixture.
    """
    from dbt_coverage.cli.orchestrator import scan

    return scan(_QUALITY_SCORE_FIXTURE_DIR)
