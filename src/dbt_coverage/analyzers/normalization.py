"""SPEC-07 §4.4 — normalized code-context helper for fingerprints."""

from __future__ import annotations

import re

from dbt_coverage.core import ParsedNode


def extract_code_context(node: ParsedNode, line: int, context_lines: int = 2) -> str:
    """Return a normalized snippet ±context_lines around ``line`` (1-indexed)."""
    lines = (node.source_sql or "").splitlines()
    if not lines:
        return ""
    i = max(1, int(line))
    lo = max(1, i - context_lines)
    hi = min(len(lines), i + context_lines)
    snippet = "\n".join(lines[lo - 1 : hi])
    return normalize_snippet(snippet)


_WS_RE = re.compile(r"\s+")


def normalize_snippet(s: str) -> str:
    """Collapse whitespace, lowercase, strip. Resilient to cosmetic reformatting."""
    return _WS_RE.sub(" ", s.strip().lower())
