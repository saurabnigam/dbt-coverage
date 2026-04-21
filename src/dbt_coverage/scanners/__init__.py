"""SPEC-03 — source scanner public exports."""

from .project_index import (
    IndexedFile,
    ModelEntry,
    ProjectIndex,
    SqlKind,
    YamlColumnMeta,
    YamlModelMeta,
    YamlSourceMeta,
)
from .source_scanner import scan
from .yaml_parser import extract_doc_blocks, parse_schema_yml

__all__ = [
    "IndexedFile",
    "ModelEntry",
    "ProjectIndex",
    "SqlKind",
    "YamlColumnMeta",
    "YamlModelMeta",
    "YamlSourceMeta",
    "scan",
    "parse_schema_yml",
    "extract_doc_blocks",
]
