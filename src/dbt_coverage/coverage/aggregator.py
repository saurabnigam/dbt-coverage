"""SPEC-09a §4.4 / SPEC-22 §9 — run all enabled coverage dimensions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dbt_coverage.core import ComplexityMetrics, CoverageMetric, ParsedNode, TestResult
from dbt_coverage.scanners import ProjectIndex

from .complexity_metric import compute_complexity_summary
from .doc_coverage import compute_doc_coverage
from .test_cc_weighted_coverage import compute_test_cc_weighted_coverage
from .test_coverage import compute_test_coverage
from .test_meaningful_coverage import compute_test_meaningful_coverage
from .test_unit_coverage import compute_test_unit_coverage

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig

_LOG = logging.getLogger(__name__)


@dataclass
class AggregatorContext:
    """SPEC-22 §9 — context bundle threaded into every coverage dimension."""

    project: ProjectIndex
    parsed_nodes: dict[str, ParsedNode] = field(default_factory=dict)
    complexity: dict[str, ComplexityMetrics] = field(default_factory=dict)
    test_results: list[TestResult] = field(default_factory=list)
    config: DbtcovConfig | None = None
    # SPEC-32 §5 — detected dbt version (from DbtTestAdapter invocation metadata).
    # Used to flag dbt < 1.8 projects in the ``test_unit`` dimension.
    dbt_version: str | None = None


DimensionFn = Callable[[AggregatorContext], CoverageMetric]


DIMENSIONS: dict[str, DimensionFn] = {
    "test": lambda ctx: compute_test_coverage(ctx.project),
    "doc": lambda ctx: compute_doc_coverage(ctx.project),
    "test_meaningful": lambda ctx: compute_test_meaningful_coverage(
        ctx.parsed_nodes, ctx.test_results, ctx.config  # type: ignore[arg-type]
    ),
    "test_weighted_cc": lambda ctx: compute_test_cc_weighted_coverage(
        ctx.parsed_nodes, ctx.complexity, ctx.test_results, ctx.config  # type: ignore[arg-type]
    ),
    # SPEC-32 §5 — always emitted; adds a ``dbt_version_below_1_8`` note on
    # older dbt projects so the zero is explained rather than hidden.
    "test_unit": lambda ctx: compute_test_unit_coverage(
        ctx.parsed_nodes, ctx.test_results, dbt_version=ctx.dbt_version
    ),
    "complexity": lambda ctx: compute_complexity_summary(
        ctx.parsed_nodes, ctx.complexity, ctx.config  # type: ignore[arg-type]
    ),
}


def compute_all(
    project_or_ctx: ProjectIndex | AggregatorContext,
    enabled: list[str] | None = None,
) -> list[CoverageMetric]:
    """Run each enabled coverage dimension and return CoverageMetric objects.

    Accepts either a bare ``ProjectIndex`` (legacy call-site: only ``test`` and
    ``doc`` are runnable) or an ``AggregatorContext`` with complexity +
    test_results + config wired in for the full five-dimension set.
    """
    if isinstance(project_or_ctx, AggregatorContext):
        ctx = project_or_ctx
    else:
        ctx = AggregatorContext(project=project_or_ctx)

    dims_to_run = enabled if enabled is not None else list(DIMENSIONS.keys())

    out: list[CoverageMetric] = []
    for dim in dims_to_run:
        fn = DIMENSIONS.get(dim)
        if fn is None:
            _LOG.warning("Unknown coverage dimension %r (skipped)", dim)
            continue
        # Dims requiring config silently skip if context has none.
        needs_config = dim in {"test_meaningful", "test_weighted_cc", "complexity"}
        if needs_config and ctx.config is None:
            _LOG.debug("Skipping dimension %r: no config available", dim)
            continue
        try:
            out.append(fn(ctx))
        except Exception as e:  # pragma: no cover - defensive
            _LOG.warning("Dimension %r crashed: %s", dim, e, exc_info=True)
    return out


__all__ = ["AggregatorContext", "DIMENSIONS", "compute_all"]
