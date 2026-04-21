"""SPEC-01 §3.1 — public re-exports of the core domain model."""

from .adapter_types import AdapterInvocation, AdapterMode
from .complexity import ComplexityMetrics
from .enums import (
    Category,
    CheckSkipReason,
    FindingType,
    RenderMode,
    Severity,
    SuppressionSource,
    TestKind,
    Tier,
)
from .exceptions import ConfigError, DbtCovError, ParseError, RenderError
from .fingerprint import compute_fingerprint
from .models import (
    AggregatedCheckSkip,
    CheckSkip,
    CheckSkipSummary,
    CoverageMetric,
    Finding,
    ModelSummary,
    RenderStats,
    ScanResult,
    Suppression,
)
from .parsed_node import ColumnDiff, ParsedNode
from .test_result import TestResult, TestStatus

__all__ = [
    "Severity",
    "Category",
    "FindingType",
    "Tier",
    "RenderMode",
    "SuppressionSource",
    "TestKind",
    "CheckSkipReason",
    "Finding",
    "Suppression",
    "CoverageMetric",
    "ScanResult",
    "RenderStats",
    "ModelSummary",
    "CheckSkip",
    "AggregatedCheckSkip",
    "CheckSkipSummary",
    "ParsedNode",
    "ColumnDiff",
    "ComplexityMetrics",
    "TestResult",
    "TestStatus",
    "AdapterInvocation",
    "AdapterMode",
    "DbtCovError",
    "ConfigError",
    "RenderError",
    "ParseError",
    "compute_fingerprint",
]
