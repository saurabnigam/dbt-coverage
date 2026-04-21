"""SPEC-01 §4.6 — ParsedNode + ColumnDiff.

Kept separate from models.py so Finding/ScanResult users don't pay for sqlglot
type hints via transitive imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import RenderMode


class ParsedNode(BaseModel):
    """One dbt model after Jinja render + SQL parse."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    file_path: Path
    node_id: str | None = None
    source_sql: str
    rendered_sql: str
    ast: Any | None = None
    line_map: dict[int, int] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    refs: list[str] = Field(default_factory=list)
    sources: list[tuple[str, str]] = Field(default_factory=list)
    macros_used: list[str] = Field(default_factory=list)
    render_mode: RenderMode
    render_uncertain: bool = False
    parse_success: bool = True
    parse_error: str | None = None
    # SPEC-25 §4.2 — populated when the node was loaded from target/compiled.
    compiled_path: Path | None = None
    # Forward-compatible compiled-line -> source-line map. V1: identity (keys==values).
    source_line_map: dict[int, int] = Field(default_factory=dict)


class ColumnDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared_only: list[str] = Field(default_factory=list)
    actual_only: list[str] = Field(default_factory=list)
    matching: list[str] = Field(default_factory=list)
