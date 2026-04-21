"""SPEC-22 §5 — classify a ``TestResult.test_kind`` into a TestClass + weight.

The classifier is deterministic and side-effect-free. Defaults are frozen
module constants; users change behaviour exclusively via ``TestOverrides``.
"""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_coverage.utils import TestOverrides, WeightTable


class TestClass(StrEnum):
    __test__ = False  # not a pytest test class

    TRIVIAL = "TRIVIAL"
    STRUCTURAL = "STRUCTURAL"
    LOGICAL = "LOGICAL"
    UNKNOWN = "UNKNOWN"


_DEFAULT_TRIVIAL: frozenset[str] = frozenset({"not_null", "unique"})
_DEFAULT_STRUCTURAL: frozenset[str] = frozenset(
    {
        "accepted_values",
        "relationships",
        "unique_combination_of_columns",
        "dbt_utils.at_least_one",
        "dbt_utils.not_constant",
        "dbt_utils.not_empty_string",
    }
)
_SINGULAR_KINDS: frozenset[str] = frozenset({"singular", "unit_test"})


def _matches(kind: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(kind, p) for p in patterns)


def _weight_for(weights: WeightTable, cls: TestClass) -> float:
    # NB: TestClass.name is uppercase; WeightTable fields are lowercase.
    return float(getattr(weights, cls.name.lower()))


def classify(
    test_kind: str | None,
    overrides: TestOverrides,
    weights: WeightTable,
) -> tuple[TestClass, float]:
    """SPEC-22 §5.2 — return (class, weight) for a test kind string.

    Precedence:
      1. User overrides (logical > structural > trivial).
      2. Built-in defaults (trivial, structural).
      3. Empty string → UNKNOWN.
      4. Anything else → LOGICAL (custom generics, singular SQL, unit_test,
         dbt_expectations.*, etc.).
    """
    k = (test_kind or "").strip()

    for cls in (TestClass.LOGICAL, TestClass.STRUCTURAL, TestClass.TRIVIAL):
        bucket = getattr(overrides, cls.name.lower())
        if bucket and _matches(k, bucket):
            return cls, _weight_for(weights, cls)

    if k in _DEFAULT_TRIVIAL:
        return TestClass.TRIVIAL, _weight_for(weights, TestClass.TRIVIAL)
    if k in _DEFAULT_STRUCTURAL:
        return TestClass.STRUCTURAL, _weight_for(weights, TestClass.STRUCTURAL)
    if not k:
        return TestClass.UNKNOWN, _weight_for(weights, TestClass.UNKNOWN)
    if k in _SINGULAR_KINDS:
        return TestClass.LOGICAL, _weight_for(weights, TestClass.LOGICAL)

    return TestClass.LOGICAL, _weight_for(weights, TestClass.LOGICAL)


__all__ = ["TestClass", "classify"]
