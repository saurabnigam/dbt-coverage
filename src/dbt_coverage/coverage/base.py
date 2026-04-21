"""SPEC-09a §4.1 — CoverageDimension protocol."""

from __future__ import annotations

from typing import Literal, Protocol

from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex

Dimension = Literal["test", "doc", "unit", "column", "pii"]


class CoverageDimensionFn(Protocol):
    def __call__(self, project: ProjectIndex) -> CoverageMetric: ...
