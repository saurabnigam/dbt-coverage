"""SPEC-01 §4.1 — enums used across the public API."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class Category(StrEnum):
    QUALITY = "QUALITY"
    PERFORMANCE = "PERFORMANCE"
    REFACTOR = "REFACTOR"
    SECURITY = "SECURITY"
    COVERAGE = "COVERAGE"
    GOVERNANCE = "GOVERNANCE"
    ARCHITECTURE = "ARCHITECTURE"
    TESTING = "TESTING"


class FindingType(StrEnum):
    BUG = "BUG"
    VULNERABILITY = "VULNERABILITY"
    CODE_SMELL = "CODE_SMELL"
    COVERAGE = "COVERAGE"
    GOVERNANCE = "GOVERNANCE"


class SuppressionSource(StrEnum):
    """SPEC-31 §3 — provenance of a :class:`Suppression`."""

    OVERRIDE = "override"
    BASELINE = "baseline"
    EXEMPTION = "exemption"


class TestKind(StrEnum):
    """SPEC-32 §3 — distinguishes dbt data tests from unit_tests (dbt 1.8+)."""

    __test__ = False

    DATA = "data"
    UNIT = "unit"
    UNKNOWN = "unknown"


class CheckSkipReason(StrEnum):
    """SPEC-33 §3 — typed reasons a (rule, node) pair was not evaluated."""

    PARSE_FAILED = "parse_failed"
    RENDER_UNCERTAIN = "render_uncertain"
    RULE_DISABLED = "rule_disabled"
    RULE_SCOPED_OUT = "rule_scoped_out"
    ADAPTER_MISSING = "adapter_missing"
    ADAPTER_FAILED = "adapter_failed"
    MODE_REQUIRED = "mode_required"
    RULE_ERROR = "rule_error"


class Tier(StrEnum):
    TIER_1_ENFORCED = "TIER_1_ENFORCED"
    TIER_2_WARN = "TIER_2_WARN"


class RenderMode(StrEnum):
    """SPEC-25 §4.1 — render strategy applied to a dbt model file.

    ``COMPILED`` reads pre-rendered SQL from ``target/compiled/**/*.sql``
    (produced by ``dbt compile``). ``DBT`` is retained as a legacy alias for
    ``COMPILED``: ``RenderMode.DBT is RenderMode.COMPILED`` and both serialise
    to the string ``"COMPILED"``.

    ``AUTO`` is a dispatcher-only value used in config; it is never set on
    a :class:`ParsedNode`. The orchestrator collapses ``AUTO`` to ``MOCK`` or
    ``COMPILED`` before any renderer is instantiated.
    """

    MOCK = "MOCK"
    PARTIAL = "PARTIAL"
    COMPILED = "COMPILED"
    AUTO = "AUTO"
    # Backward-compatibility alias — matching value folds into COMPILED.
    DBT = "COMPILED"
