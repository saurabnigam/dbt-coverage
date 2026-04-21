"""SPEC-21 §4 & §5 — Adapter Protocol + AdapterResult + AdapterConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from dbt_coverage.core import (
    AdapterInvocation,
    AdapterMode,
    CoverageMetric,
    Finding,
    TestResult,
)


class AdapterConfig(BaseModel):
    """Per-adapter config block (``adapters.<name>`` in dbtcov.yml)."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    mode: AdapterMode = AdapterMode.AUTO
    report: Path | None = None
    timeout_seconds: int = Field(default=60, ge=1)
    argv: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class AdapterResult(BaseModel):
    """Output of a single adapter invocation."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    adapter: str
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[CoverageMetric] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    invocation: AdapterInvocation


@runtime_checkable
class Adapter(Protocol):
    """SPEC-21 §5. Adapters must expose these attributes and methods."""

    name: str
    display_name: str
    output_kinds: tuple[str, ...]
    default_report_path: Path | None
    default_mode: AdapterMode

    def discover(self, project_root: Path, cfg: AdapterConfig) -> Path | None: ...

    def is_runnable(self) -> bool: ...

    def run(self, project_root: Path, cfg: AdapterConfig) -> Path: ...

    def read(self, report_path: Path, cfg: AdapterConfig) -> AdapterResult: ...

    def tool_version(self) -> str | None: ...
