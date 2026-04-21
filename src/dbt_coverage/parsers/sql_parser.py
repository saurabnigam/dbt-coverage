"""SPEC-06 — sqlglot parser with a 3-step error-recovery ladder."""

from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot.errors import ParseError as SqlglotParseError

from dbt_coverage.core import ParsedNode

from .sqlglot_dialects import validate_dialect

_LOG = logging.getLogger(__name__)

# Pattern for stripping obviously-broken __MACRO_*__ identifiers at statement
# starts that are left from an uncertain render.
_BAD_MACRO_LINE_RE = re.compile(r"^[\s;]*__MACRO_\w+__\s*$", re.MULTILINE)


class SqlParser:
    """Parse ``ParsedNode.rendered_sql`` into an AST, recovering on failure."""

    def __init__(self, dialect: str) -> None:
        self.dialect = validate_dialect(dialect)

    # ----------------------------------------------------------- public API

    def parse(self, node: ParsedNode) -> ParsedNode:
        """Populate ``node.ast``, never raise."""
        rendered = node.rendered_sql or ""
        if not rendered.strip():
            node.ast = None
            node.parse_success = False
            node.parse_error = "empty input"
            return node

        attempt1_err: str | None = None
        try:
            node.ast = sqlglot.parse_one(rendered, read=self.dialect)
            node.parse_success = True
            node.parse_error = None
            return node
        except SqlglotParseError as e:
            attempt1_err = str(e)
        except Exception as e:
            attempt1_err = f"unexpected: {e}"

        # Attempt 2: dialect-free
        try:
            node.ast = sqlglot.parse_one(rendered, read=None)
            node.parse_success = True
            node.parse_error = None
            _LOG.debug("Parsed %s with dialect=None after %s failed", node.file_path, self.dialect)
            return node
        except Exception:
            pass

        # Attempt 3: sanitize known problem tokens, retry original dialect
        sanitized = _BAD_MACRO_LINE_RE.sub("SELECT 1", rendered)
        sanitized = _truncate_unclosed_cte(sanitized)
        if sanitized.strip() and sanitized != rendered:
            ratio = len(sanitized) / max(len(rendered), 1)
            try:
                if ratio >= 0.5:
                    node.ast = sqlglot.parse_one(sanitized, read=self.dialect)
                    node.parse_success = True
                    node.parse_error = None
                    return node
            except Exception:
                pass

        node.ast = None
        node.parse_success = False
        node.parse_error = attempt1_err or "parse failed"
        return node

    def parse_all(self, nodes: list[ParsedNode]) -> list[ParsedNode]:
        return [self.parse(n) for n in nodes]


def _truncate_unclosed_cte(sql: str) -> str:
    """If the text ends inside an unclosed CTE (`WITH x AS (` with no matching
    close), drop it back to the last newline before the open. Best-effort.
    """
    # Count unbalanced parens ignoring those inside quoted strings. Keep simple:
    # if open-parens > close-parens at end, truncate progressively by lines.
    lines = sql.splitlines()
    while lines and sql.count("(") > sql.count(")"):
        lines.pop()
        sql = "\n".join(lines)
    return sql
