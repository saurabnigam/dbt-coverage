"""Column-level test coverage: (columns with ≥1 test) / (total declared columns) per model."""

from __future__ import annotations

from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex


def compute_test_column_coverage(project: ProjectIndex) -> CoverageMetric:
    """Compute the ``column_test`` dimension.

    For each model that has YAML metadata with declared columns, count how many
    columns have at least one test vs total columns. Models with zero declared
    columns are excluded (per_node entry omitted) to avoid penalising models
    that simply haven't been documented in schema.yml yet.
    """
    per_node: dict[str, tuple[int, int]] = {}
    covered = 0
    total = 0

    for node_id, entry in project.models.items():
        yml = entry.yml_meta
        if yml is None:
            continue
        cols = yml.columns
        if not cols:
            # No columns declared — exempt this model
            continue
        col_total = len(cols)
        col_covered = sum(1 for col in cols if col.tests)
        per_node[node_id] = (col_covered, col_total)
        covered += col_covered
        total += col_total

    ratio = (covered / total) if total > 0 else 1.0
    return CoverageMetric(
        dimension="column_test",
        covered=covered,
        total=total,
        ratio=ratio,
        per_node=per_node,
    )
