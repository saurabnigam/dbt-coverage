"""Column-level meaningful test coverage: (columns with ≥1 LOGICAL passing test) / (total declared columns)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_coverage.core import CoverageMetric, ParsedNode, TestKind, TestResult, TestStatus
from dbt_coverage.scanners import ProjectIndex

from .test_classifier import classify

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig


def compute_test_column_meaningful_coverage(
    project: ProjectIndex,
    test_results: list[TestResult],
    cfg: DbtcovConfig,
) -> CoverageMetric:
    """Compute the ``column_test_meaningful`` dimension.

    For each model with declared columns, count how many columns have at least
    one LOGICAL-weight test that passed. This excludes trivial tests like
    not_null/unique — only domain-specific, meaningful tests count.
    """
    # If no test results available (no adapter run), we cannot assess meaningful
    # column coverage — return an empty metric with no per_node entries so models
    # are not penalised for lack of adapter data.
    if not test_results:
        return CoverageMetric(
            dimension="column_test_meaningful",
            covered=0,
            total=0,
            ratio=1.0,
            per_node={},
        )

    weights = cfg.coverage.weights
    overrides = cfg.coverage.test_overrides
    logical_threshold = weights.logical

    # Only DATA tests contribute (unit tests are model-level, not column-level)
    data_results = [tr for tr in test_results if tr.kind != TestKind.UNIT]

    # Determine if we have real status info
    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in data_results)

    # Index test results by (model_unique_id, column_name) — deduplicate keeping worst status
    dedup: dict[tuple[str | None, str | None, str], TestResult] = {}
    for tr in data_results:
        if not tr.column_name:
            continue
        key = (tr.model_unique_id, tr.column_name.lower(), tr.test_name)
        existing = dedup.get(key)
        if existing is None or _status_rank(tr.status) > _status_rank(existing.status):
            dedup[key] = tr

    # Build set of (model_id, column_name_lower) that have qualifying tests
    qualified_columns: set[tuple[str, str]] = set()
    for tr in dedup.values():
        if tr.model_unique_id is None:
            continue
        _, w = classify(tr.test_kind, overrides, weights)
        if w < logical_threshold:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        qualified_columns.add((tr.model_unique_id, (tr.column_name or "").lower()))

    per_node: dict[str, tuple[int, int]] = {}
    covered = 0
    total = 0

    for node_id, entry in project.models.items():
        yml = entry.yml_meta
        if yml is None:
            continue
        cols = yml.columns
        if not cols:
            continue
        col_total = len(cols)
        col_covered = sum(
            1 for col in cols
            if (node_id, col.name.lower()) in qualified_columns
        )
        per_node[node_id] = (col_covered, col_total)
        covered += col_covered
        total += col_total

    ratio = (covered / total) if total > 0 else 0.0
    return CoverageMetric(
        dimension="column_test_meaningful",
        covered=covered,
        total=total,
        ratio=ratio,
        per_node=per_node,
    )


_STATUS_RANK: dict[TestStatus, int] = {
    TestStatus.UNKNOWN: 0,
    TestStatus.PASS: 1,
    TestStatus.SKIPPED: 2,
    TestStatus.ERROR: 3,
    TestStatus.FAIL: 4,
}


def _status_rank(s: TestStatus) -> int:
    return _STATUS_RANK.get(s, 0)
