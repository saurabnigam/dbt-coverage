"""SPEC-02 §4.3 — adapter → sqlglot dialect mapping."""

from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)

ADAPTER_TO_SQLGLOT_DIALECT: dict[str, str] = {
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "postgres": "postgres",
    "redshift": "redshift",
    "databricks": "databricks",
    "spark": "spark",
    "duckdb": "duckdb",
    "trino": "trino",
    "athena": "athena",
    "mysql": "mysql",
    "oracle": "oracle",
}

_DEFAULT_DIALECT = "postgres"


def resolve_dialect(config_dialect: str | None, adapter: str | None) -> str:
    """
    Precedence: config_dialect > ADAPTER_TO_SQLGLOT_DIALECT[adapter] > "postgres".
    """
    if config_dialect:
        return config_dialect.lower()
    if adapter:
        mapped = ADAPTER_TO_SQLGLOT_DIALECT.get(adapter.lower())
        if mapped:
            return mapped
        _LOG.warning(
            "Unknown adapter %r; defaulting sqlglot dialect to %s",
            adapter,
            _DEFAULT_DIALECT,
        )
    return _DEFAULT_DIALECT
