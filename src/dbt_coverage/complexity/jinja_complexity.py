"""SPEC-19 §5.2 — raw-source Jinja decision-point scanner.

Intentionally a conservative regex — goal is to count decision points the
author wrote, not to simulate Jinja execution. Macros are not expanded;
callers are not charged for macro-internal branches.
"""

from __future__ import annotations

import re

_IF_RE = re.compile(r"\{%-?\s*(?:if|elif)\b", re.IGNORECASE)
_FOR_RE = re.compile(r"\{%-?\s*for\b", re.IGNORECASE)


def compute_jinja_cc(source: str) -> dict[str, int]:
    if not source:
        return {"jinja_ifs": 0, "jinja_fors": 0}
    return {
        "jinja_ifs": len(_IF_RE.findall(source)),
        "jinja_fors": len(_FOR_RE.findall(source)),
    }
