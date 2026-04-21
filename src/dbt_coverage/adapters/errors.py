"""SPEC-21 — adapter-specific errors."""

from __future__ import annotations

from dbt_coverage.core import DbtCovError


class AdapterError(DbtCovError):
    """Base for adapter-layer errors."""


class AdapterNotRunnableError(AdapterError):
    """Raised when an adapter cannot produce output in the requested mode."""


class AdapterTimeoutError(AdapterError):
    """Raised when an adapter's subprocess exceeds its timeout."""


class UnsupportedSchemaError(AdapterError):
    """Raised when an adapter encounters an artefact whose schema version it cannot parse."""

    def __init__(self, schema_version: int, tool: str = "") -> None:
        super().__init__(
            f"Unsupported {tool or 'artefact'} schema version: v{schema_version}"
        )
        self.schema_version = schema_version
