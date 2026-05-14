"""CC-weighted unit test coverage: unit test coverage weighted by model complexity.

High-complexity models without unit tests drag the overall ratio down
proportionally, incentivising unit tests on the most complex logic.
"""

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

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig

# Default: models below this CC are auto-covered (too simple to require unit tests)
_DEFAULT_UNIT_CC_THRESHOLD = 3


def compute_test_unit_cc_weighted_coverage(
    parsed_nodes: dict[str, ParsedNode],
    complexity: dict[str, ComplexityMetrics],
    test_results: list[TestResult],
    cfg: DbtcovConfig | None = None,
    dbt_version: str | None = None,
) -> CoverageMetric:
    """Compute the ``test_unit_weighted_cc`` dimension.

    Formula:
        ratio = sum(has_unit(m) * cc(m)) / sum(cc(m))

    Where has_unit(m) is 1.0 if:
      - the model has ≥1 passing UNIT test, OR
      - the model's CC is below the configured threshold (auto-covered)
    """
    # Read configurable threshold from coverage config
    cc_threshold = _DEFAULT_UNIT_CC_THRESHOLD
    if cfg and hasattr(cfg.coverage, "unit_test_cc_threshold"):
        cc_threshold = cfg.coverage.unit_test_cc_threshold

    # Only UNIT tests contribute
    unit_results = [tr for tr in test_results if tr.kind == TestKind.UNIT]
    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in unit_results)

    model_ids = {nid for nid in parsed_nodes if nid.startswith("model.")}
    cc_by_model: dict[str, int] = {
        m: (complexity[m].cc if m in complexity else 1) for m in model_ids
    }

    # Collect models that have ≥1 passing unit test
    covered_models: set[str] = set()
    for tr in unit_results:
        if tr.model_unique_id is None or tr.model_unique_id not in model_ids:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        covered_models.add(tr.model_unique_id)

    # Compute weighted coverage
    # Models below CC threshold are auto-covered (they're too simple to need unit tests)
    has_unit: dict[str, float] = {}
    for m in model_ids:
        if m in covered_models:
            has_unit[m] = 1.0
        elif cc_by_model[m] <= cc_threshold:
            has_unit[m] = 1.0  # auto-covered: low complexity
        else:
            has_unit[m] = 0.0

    num = sum(has_unit[m] * cc_by_model[m] for m in model_ids)
    den_raw = sum(cc_by_model.values())
    ratio = (num / den_raw) if den_raw > 0 else 0.0

    # Clamp defensively
    ratio = max(0.0, min(1.0, ratio))

    per_node = {
        m: (int(round(has_unit[m] * cc_by_model[m])), cc_by_model[m]) for m in model_ids
    }

    covered_int = int(round(num))
    total_int = int(den_raw)
    if covered_int > total_int:
        covered_int = total_int

    notes: list[str] = []
    if _below_1_8(dbt_version):
        notes.append("dbt_version_below_1_8")

    metric = CoverageMetric(
        dimension="test_unit_weighted_cc",
        covered=covered_int,
        total=total_int,
        ratio=ratio,
        per_node=per_node,
    )
    if notes:
        metric.notes = notes
    return metric


def _below_1_8(version: str | None) -> bool:
    if not version:
        return False
    parts = version.split(".")
    if len(parts) < 2:
        return False
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return (major, minor) < (1, 8)
