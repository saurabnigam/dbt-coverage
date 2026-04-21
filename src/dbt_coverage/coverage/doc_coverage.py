"""SPEC-09a §4.3 — combined model + column doc coverage."""

from __future__ import annotations

from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex


def _is_doc_present(desc: str | None) -> bool:
    if desc is None:
        return False
    return bool(desc.strip())


def compute_doc_coverage(project: ProjectIndex) -> CoverageMetric:
    per_node: dict[str, tuple[int, int]] = {}
    total_covered = 0
    total_total = 0

    for node_id, entry in project.models.items():
        yml = entry.yml_meta
        if yml is None:
            per_node[node_id] = (0, 1)
            total_total += 1
            continue

        model_covered = 1 if _is_doc_present(yml.description) else 0
        col_total = len(yml.columns)
        col_covered = sum(1 for c in yml.columns if _is_doc_present(c.description))

        node_covered = model_covered + col_covered
        node_total = 1 + col_total
        per_node[node_id] = (node_covered, node_total)
        total_covered += node_covered
        total_total += node_total

    ratio = (total_covered / total_total) if total_total > 0 else 1.0
    return CoverageMetric(
        dimension="doc",
        covered=total_covered,
        total=total_total,
        ratio=ratio,
        per_node=per_node,
    )
