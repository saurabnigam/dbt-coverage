"""SPEC-32 §5 — ``test_unit`` coverage dimension.

Counts models that have at least one ``TestKind.UNIT`` test defined. The
dimension is *always* rendered so dbt < 1.8 projects see an explicit
``0 / N`` with a trailing note rather than a silent omission.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_coverage.core import CoverageMetric, ParsedNode, TestKind, TestResult

if TYPE_CHECKING:
    pass


def compute_test_unit_coverage(
    parsed_nodes: dict[str, ParsedNode],
    test_results: list[TestResult],
    dbt_version: str | None = None,
) -> CoverageMetric:
    """Compute the ``test_unit`` dimension.

    Parameters
    ----------
    parsed_nodes
        Discovered dbt models, keyed by ``unique_id`` (``model.<pkg>.<name>``).
    test_results
        Full ``TestResult`` list; only ``TestKind.UNIT`` entries contribute.
    dbt_version
        Optional dbt version string from the adapter invocation. When the
        version is known to be < 1.8 (no unit-test support), the metric is
        emitted with ``notes`` explaining the zero instead of hiding it.
    """
    model_ids = {nid for nid in parsed_nodes if nid.startswith("model.")}
    total = len(model_ids)

    covered: set[str] = set()
    for tr in test_results:
        if tr.kind != TestKind.UNIT:
            continue
        if tr.model_unique_id and tr.model_unique_id in model_ids:
            covered.add(tr.model_unique_id)

    per_node = {m: (1 if m in covered else 0, 1) for m in model_ids}
    ratio = (len(covered) / total) if total > 0 else 0.0

    notes: list[str] = []
    if _below_1_8(dbt_version):
        notes.append("dbt_version_below_1_8")

    metric = CoverageMetric(
        dimension="test_unit",
        covered=len(covered),
        total=total,
        ratio=ratio,
        per_node=per_node,
    )
    if notes and hasattr(metric, "notes"):
        # CoverageMetric may carry a notes field in a future revision.
        metric.notes = notes  # type: ignore[attr-defined]
    return metric


def _below_1_8(version: str | None) -> bool:
    """``True`` when ``version`` is a parseable string like ``"1.7.12"``."""
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
