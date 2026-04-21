"""SPEC-21 — public surface for the adapter framework."""

from .base import Adapter, AdapterConfig, AdapterResult
from .dbt_test import DbtTestAdapter
from .dedup import merge_findings
from .errors import (
    AdapterError,
    AdapterNotRunnableError,
    AdapterTimeoutError,
    UnsupportedSchemaError,
)
from .scheduler import run_adapters
from .sqlfluff import SqlfluffAdapter


def builtin_adapters() -> list[Adapter]:
    """Return instances of every built-in adapter, in a stable order."""
    return [DbtTestAdapter(), SqlfluffAdapter()]


__all__ = [
    "Adapter",
    "AdapterConfig",
    "AdapterResult",
    "AdapterError",
    "AdapterNotRunnableError",
    "AdapterTimeoutError",
    "UnsupportedSchemaError",
    "DbtTestAdapter",
    "SqlfluffAdapter",
    "merge_findings",
    "run_adapters",
    "builtin_adapters",
]
