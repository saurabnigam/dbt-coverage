"""SPEC-05 + SPEC-06 — Jinja renderer + SQL parser."""

from .compiled_renderer import CompiledRenderer
from .jinja_renderer import JinjaRenderer
from .line_map import extract_line_map, inject_line_markers
from .macro_indexer import MacroRegistry, index_macros
from .mock_context import (
    AdapterDispatchUnsupported,
    CapturedConfig,
    build_mock_context,
)
from .sql_parser import SqlParser
from .sqlglot_dialects import validate_dialect

__all__ = [
    "CompiledRenderer",
    "JinjaRenderer",
    "SqlParser",
    "validate_dialect",
    "inject_line_markers",
    "extract_line_map",
    "MacroRegistry",
    "index_macros",
    "CapturedConfig",
    "AdapterDispatchUnsupported",
    "build_mock_context",
]
