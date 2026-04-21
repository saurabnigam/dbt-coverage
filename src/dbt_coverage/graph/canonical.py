"""SPEC-18 §4.3 — canonical AST via sqlglot optimizer (safe subset)."""

from __future__ import annotations

import logging
from typing import Any

from sqlglot import expressions as exp
from sqlglot.optimizer.normalize import normalize
from sqlglot.optimizer.qualify_columns import qualify_columns

_LOG = logging.getLogger(__name__)


def canonicalize(ast: Any, dialect: str) -> Any | None:
    """Apply a conservative optimizer pipeline. Returns None on failure."""
    if ast is None:
        return None
    try:
        tree = ast.copy()
        if isinstance(tree, exp.Select):
            try:
                tree = qualify_columns(tree, schema=None, dialect=dialect)  # type: ignore[arg-type]
            except Exception as e:
                _LOG.debug("qualify_columns failed: %s", e)
        try:
            tree = normalize(tree)
        except Exception as e:
            _LOG.debug("normalize failed: %s", e)
        return tree
    except Exception as e:
        _LOG.debug("canonicalize failed: %s", e)
        return None
