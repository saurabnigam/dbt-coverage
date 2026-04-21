"""Shared pytest fixtures — SPEC-13."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_dbt_project"


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
