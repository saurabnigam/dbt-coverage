"""SPEC-06 §4.3 — sqlglot dialect validation."""

from __future__ import annotations

from dbt_coverage.core import ConfigError

_VALID_DIALECTS: set[str] = {
    "snowflake",
    "bigquery",
    "postgres",
    "redshift",
    "databricks",
    "spark",
    "duckdb",
    "trino",
    "athena",
    "mysql",
    "tsql",
    "oracle",
}


def validate_dialect(dialect: str) -> str:
    """Return canonical lowercase dialect name or raise ConfigError."""
    norm = (dialect or "").lower().strip()
    if norm not in _VALID_DIALECTS:
        hint = ""
        if norm == "mssql":
            hint = " (did you mean 'tsql'?)"
        raise ConfigError(f"Unknown sqlglot dialect: {dialect!r}{hint}")
    return norm
