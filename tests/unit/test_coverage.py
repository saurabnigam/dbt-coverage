"""Unit tests for coverage calculator."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.coverage import compute_all


def test_coverage_on_sample_project(sample_project: Path) -> None:
    from dbt_coverage.scanners import scan as scan_sources
    from dbt_coverage.utils import load_config, load_project_info

    info = load_project_info(sample_project)
    cfg = load_config(sample_project)
    project = scan_sources(info, cfg)

    metrics = compute_all(project)
    dims = {m.dimension: m for m in metrics}
    assert "test" in dims
    assert "doc" in dims
    test_m = dims["test"]
    doc_m = dims["doc"]
    assert test_m.total >= 4
    assert 0.0 <= test_m.ratio <= 1.0
    assert doc_m.total >= 4
    assert 0.0 <= doc_m.ratio <= 1.0
