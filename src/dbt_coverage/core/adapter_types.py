"""SPEC-21 §4.1 / §4.3 — adapter-related enums + provenance records.

Lives in core so both the Adapter framework (adapters/) and ScanResult
can import without creating cycles.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AdapterMode(StrEnum):
    READ = "read"
    RUN = "run"
    AUTO = "auto"


class AdapterInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    mode: AdapterMode
    tool_version: str | None = None
    argv: list[str] = Field(default_factory=list)
    report_path: Path | None = None
    started_at_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    status: str = "ok"
    message: str | None = None
    # SPEC-32 §4 — adapter-specific metadata, e.g. ``dbt_version`` so
    # downstream coverage / rules can gate unit-test logic on dbt ≥ 1.8.
    metadata: dict[str, str] = Field(default_factory=dict)
