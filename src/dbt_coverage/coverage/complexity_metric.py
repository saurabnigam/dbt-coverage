"""SPEC-22 §8 — ``complexity`` coverage dimension (cross-ref SPEC-19 §7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_coverage.core import ComplexityMetrics, CoverageMetric, ParsedNode

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig


def compute_complexity_summary(
    parsed_nodes: dict[str, ParsedNode],
    complexity: dict[str, ComplexityMetrics],
    cfg: DbtcovConfig,
) -> CoverageMetric:
    threshold = cfg.complexity.threshold_warn
    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n, nid)}
    under: set[str] = set()
    for m in model_ids:
        cc_val = complexity[m].cc if m in complexity else 1
        if cc_val <= threshold:
            under.add(m)
    total = len(model_ids)
    per_node = {m: (1 if m in under else 0, 1) for m in model_ids}
    ratio = (len(under) / total) if total > 0 else 0.0
    return CoverageMetric(
        dimension="complexity",
        covered=len(under),
        total=total,
        ratio=ratio,
        per_node=per_node,
    )


def _is_model(node: ParsedNode, node_id: str | None) -> bool:
    if node_id and node_id.startswith("model."):
        return True
    nid = node.node_id or ""
    return nid.startswith("model.") or True
