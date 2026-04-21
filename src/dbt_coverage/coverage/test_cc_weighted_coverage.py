"""SPEC-22 §7 — ``test_weighted_cc`` coverage dimension."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_coverage.core import (
    ComplexityMetrics,
    CoverageMetric,
    ParsedNode,
    TestKind,
    TestResult,
    TestStatus,
)

from .test_classifier import classify

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig


def compute_test_cc_weighted_coverage(
    parsed_nodes: dict[str, ParsedNode],
    complexity: dict[str, ComplexityMetrics],
    test_results: list[TestResult],
    cfg: DbtcovConfig,
) -> CoverageMetric:
    weights = cfg.coverage.weights
    overrides = cfg.coverage.test_overrides
    # SPEC-32 §5 — ``test_weighted_cc`` scores DATA tests only.
    test_results = [tr for tr in test_results if tr.kind != TestKind.UNIT]
    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in test_results)

    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n, nid)}
    cc_by_model: dict[str, int] = {
        m: (complexity[m].cc if m in complexity else 1) for m in model_ids
    }

    best_w: dict[str, float] = {m: 0.0 for m in model_ids}
    # Dedup by (test_name, model_unique_id) preserving worst status.
    dedup: dict[tuple[str, str | None], TestResult] = {}
    for tr in test_results:
        key = (tr.test_name, tr.model_unique_id)
        existing = dedup.get(key)
        if existing is None or _status_rank(tr.status) > _status_rank(existing.status):
            dedup[key] = tr

    for tr in dedup.values():
        if tr.model_unique_id is None or tr.model_unique_id not in model_ids:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        _, w = classify(tr.test_kind, overrides, weights)
        if w > best_w[tr.model_unique_id]:
            best_w[tr.model_unique_id] = w

    num = sum(best_w[m] * cc_by_model[m] for m in model_ids)
    den_raw = sum(cc_by_model.values())
    ratio = (num / den_raw) if den_raw > 0 else 0.0
    # Clamp to [0,1] defensively.
    if ratio < 0.0:
        ratio = 0.0
    elif ratio > 1.0:
        ratio = 1.0

    per_node = {
        m: (int(round(best_w[m] * cc_by_model[m])), cc_by_model[m]) for m in model_ids
    }

    covered_int = int(round(num))
    total_int = int(den_raw)
    if covered_int > total_int:
        covered_int = total_int
    return CoverageMetric(
        dimension="test_weighted_cc",
        covered=covered_int,
        total=total_int,
        ratio=ratio,
        per_node=per_node,
    )


def _is_model(node: ParsedNode, node_id: str | None) -> bool:
    if node_id and node_id.startswith("model."):
        return True
    nid = node.node_id or ""
    return nid.startswith("model.") or True


_STATUS_RANK: dict[TestStatus, int] = {
    TestStatus.UNKNOWN: 0,
    TestStatus.PASS: 1,
    TestStatus.SKIPPED: 2,
    TestStatus.ERROR: 3,
    TestStatus.FAIL: 4,
}


def _status_rank(s: TestStatus) -> int:
    return _STATUS_RANK.get(s, 0)
