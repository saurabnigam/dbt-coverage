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

    # Parse-failed models have no real AST — their CC defaults to 1 (below any
    # threshold), giving a spurious ✓.  Exclude them from both numerator and
    # denominator so the aggregate ratio is accurate and the UI shows "—".
    # Uncertain-render models are included with their (approximate) CC because
    # partial SQL analysis is still informative.
    assessable = {nid for nid in model_ids if parsed_nodes[nid].parse_success}

    under: set[str] = set()
    for m in assessable:
        cc_val = complexity[m].cc if m in complexity else 1
        if cc_val <= threshold:
            under.add(m)

    total = len(assessable)
    per_node: dict[str, tuple[int, int]] = {}
    for m in assessable:
        per_node[m] = (1 if m in under else 0, 1)
    # Parse-failed models get (0, 0) — the UI dimCell renders this as "—"
    for nid in model_ids - assessable:
        per_node[nid] = (0, 0)

    ratio = (len(under) / total) if total > 0 else 0.0
    return CoverageMetric(
        dimension="complexity",
        covered=len(under),
        total=total,
        ratio=ratio,
        per_node=per_node,
    )


def _is_model(node: ParsedNode, node_id: str | None) -> bool:
    nid = node_id or node.node_id or ""
    return nid.startswith("model.")
