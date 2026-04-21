"""SPEC-09a §4.2 — test coverage: fraction of models with ≥1 declared test."""

from __future__ import annotations

from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex


def compute_test_coverage(project: ProjectIndex) -> CoverageMetric:
    per_node: dict[str, tuple[int, int]] = {}
    covered = 0
    total = 0
    for node_id, entry in project.models.items():
        total += 1
        yml = entry.yml_meta
        is_covered = False
        if yml is not None:
            if yml.tests:
                is_covered = True
            else:
                for col in yml.columns:
                    if col.tests:
                        is_covered = True
                        break
        per_node[node_id] = (1 if is_covered else 0, 1)
        if is_covered:
            covered += 1
    ratio = (covered / total) if total > 0 else 1.0
    return CoverageMetric(
        dimension="test", covered=covered, total=total, ratio=ratio, per_node=per_node
    )
