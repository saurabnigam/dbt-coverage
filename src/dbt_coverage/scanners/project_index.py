"""SPEC-03 §4.1 — ProjectIndex, IndexedFile, ModelEntry, YAML metadata types."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SqlKind = Literal["model", "singular_test", "macro", "seed", "snapshot", "analysis"]


class IndexedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path  # relative to project_root
    absolute_path: Path
    content: str
    source_hash: str


class YamlColumnMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    tests: list[Any] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class YamlModelMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    columns: list[YamlColumnMeta] = Field(default_factory=list)
    tests: list[Any] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    unit_tests: list[Any] = Field(default_factory=list)
    file_path: Path
    line: int = 1


class YamlSourceMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_name: str
    table_name: str
    description: str | None = None
    columns: list[YamlColumnMeta] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    file_path: Path
    line: int = 1


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    name: str
    sql_file: IndexedFile
    yml_meta: YamlModelMeta | None = None


class ProjectIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_root: Path
    project_name: str
    models: dict[str, ModelEntry] = Field(default_factory=dict)
    singular_tests: list[IndexedFile] = Field(default_factory=list)
    macros: list[IndexedFile] = Field(default_factory=list)
    seeds: list[IndexedFile] = Field(default_factory=list)
    sources: dict[tuple[str, str], YamlSourceMeta] = Field(default_factory=dict)
    exposures: list[Any] = Field(default_factory=list)
    yml_files: list[IndexedFile] = Field(default_factory=list)
    doc_blocks: dict[str, str] = Field(default_factory=dict)
    scan_errors: list[str] = Field(default_factory=list)
