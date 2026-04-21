"""SPEC-21 §4.2 — TestResult / TestStatus.

Tool-agnostic representation of a single test outcome. Emitted by
adapters (dbt-test, custom runners, etc.), consumed by SPEC-22 weighted
coverage. Never mutates core scanner state.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .enums import TestKind


class TestStatus(StrEnum):
    __test__ = False  # not a pytest test class

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class TestResult(BaseModel):
    __test__ = False  # not a pytest test class

    model_config = ConfigDict(extra="forbid", frozen=True)

    test_name: str
    test_kind: str
    model_unique_id: str | None = None
    column_name: str | None = None
    status: TestStatus = TestStatus.UNKNOWN
    file_path: Path | None = None
    origin: str
    raw_kind: str | None = None
    # SPEC-32 §3 — DATA (resource_type=test) vs UNIT (resource_type=unit_test).
    kind: TestKind = TestKind.UNKNOWN
    # SPEC-32 §3 — False when the test is defined in manifest but never ran.
    executed: bool = True
    # SPEC-32 §6.T003 — populated only for malformed unit tests.
    malformed_reason: str | None = None
