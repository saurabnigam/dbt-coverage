"""SPEC-22 §6 — ``test_meaningful`` coverage dimension."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_coverage.core import CoverageMetric, ParsedNode, TestKind, TestResult, TestStatus

from .test_classifier import classify

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig


def compute_test_meaningful_coverage(
    parsed_nodes: dict[str, ParsedNode],
    test_results: list[TestResult],
    cfg: DbtcovConfig,
) -> CoverageMetric:
    weights = cfg.coverage.weights
    overrides = cfg.coverage.test_overrides
    logical_threshold = weights.logical

    # SPEC-32 §5 — ``test_meaningful`` scores row-level assertions only.
    # Unit tests are accounted for by the dedicated ``test_unit`` dimension.
    test_results = [tr for tr in test_results if tr.kind != TestKind.UNIT]

    # Only count a test as "passing" when at least one adapter reported a real
    # status. If every TestResult is UNKNOWN, fall back to declared-only mode.
    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in test_results)

    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n, nid)}

    # Dedup TestResults for same (test_name, model_unique_id): keep worst status.
    dedup: dict[tuple[str, str | None], TestResult] = {}
    for tr in test_results:
        key = (tr.test_name, tr.model_unique_id)
        existing = dedup.get(key)
        if existing is None or _status_rank(tr.status) > _status_rank(existing.status):
            dedup[key] = tr

    covered: set[str] = set()
    for tr in dedup.values():
        if tr.model_unique_id is None or tr.model_unique_id not in model_ids:
            continue
        _, w = classify(tr.test_kind, overrides, weights)
        if w < logical_threshold:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        covered.add(tr.model_unique_id)

    # Declared-only fallback: if we never saw a TestResult, mimic SPEC-09a by
    # reusing the project index attached to the context (see aggregator).
    total = len(model_ids)
    per_node = {m: (1 if m in covered else 0, 1) for m in model_ids}
    ratio = (len(covered) / total) if total > 0 else 0.0
    return CoverageMetric(
        dimension="test_meaningful",
        covered=len(covered),
        total=total,
        ratio=ratio,
        per_node=per_node,
    )


def _is_model(node: ParsedNode, node_id: str | None) -> bool:
    if node_id and node_id.startswith("model."):
        return True
    # Fall back to node's own node_id.
    nid = node.node_id or ""
    return nid.startswith("model.") or True  # all parsed nodes are models today


_STATUS_RANK: dict[TestStatus, int] = {
    TestStatus.UNKNOWN: 0,
    TestStatus.PASS: 1,
    TestStatus.SKIPPED: 2,
    TestStatus.ERROR: 3,
    TestStatus.FAIL: 4,
}


def _status_rank(s: TestStatus) -> int:
    return _STATUS_RANK.get(s, 0)
