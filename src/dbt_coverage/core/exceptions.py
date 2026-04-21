"""SPEC-01 §4.8 — exception hierarchy."""

from __future__ import annotations


class DbtCovError(Exception):
    """Base for all dbt-coverage-lib errors."""


class ConfigError(DbtCovError):
    """Raised for invalid configuration or project discovery failures."""


class RenderError(DbtCovError):
    """Raised for unrecoverable Jinja render failures (rare — most are handled as uncertain)."""


class ParseError(DbtCovError):
    """Raised for unrecoverable SQL parse failures (rare — the parser ladder usually handles it)."""
