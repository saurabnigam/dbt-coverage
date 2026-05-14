"""SPEC-09a / SPEC-22 — coverage calculator public API."""

from .aggregator import DIMENSIONS, AggregatorContext, compute_all
from .complexity_metric import compute_complexity_summary
from .doc_coverage import compute_doc_coverage
from .test_cc_weighted_coverage import compute_test_cc_weighted_coverage
from .test_classifier import TestClass, classify
from .test_column_coverage import compute_test_column_coverage
from .test_column_meaningful_coverage import compute_test_column_meaningful_coverage
from .test_coverage import compute_test_coverage
from .test_meaningful_coverage import compute_test_meaningful_coverage
from .test_unit_cc_weighted_coverage import compute_test_unit_cc_weighted_coverage

__all__ = [
    "AggregatorContext",
    "DIMENSIONS",
    "TestClass",
    "classify",
    "compute_all",
    "compute_complexity_summary",
    "compute_doc_coverage",
    "compute_test_cc_weighted_coverage",
    "compute_test_column_coverage",
    "compute_test_column_meaningful_coverage",
    "compute_test_coverage",
    "compute_test_meaningful_coverage",
    "compute_test_unit_cc_weighted_coverage",
]
