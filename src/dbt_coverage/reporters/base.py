"""SPEC-10a §4.1 — Reporter Protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dbt_coverage.core import ScanResult


class Reporter(Protocol):
    name: str
    default_filename: str | None

    def emit(self, result: ScanResult, out: Path | None = None) -> None: ...
