"""SPEC-24 §6 — sqlfluff JSON parser.

Handles sqlfluff 2.x (``line_no``/``line_pos``) and 3.x (``start_line_no``/
``start_line_pos``). Accepts the top-level list form and the legacy
``{"files": [...]}`` wrapper.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlfluffViolation:
    file_path: Path
    line: int
    column: int
    code: str
    name: str
    description: str
    is_warning: bool


def parse_sqlfluff_json(text: str) -> list[SqlfluffViolation]:
    if not text or not text.strip():
        return []

    data = json.loads(text)
    if isinstance(data, dict) and "files" in data:
        data = data["files"]
    if not isinstance(data, list):
        _LOG.warning("sqlfluff JSON not a list (%s); treating as empty", type(data).__name__)
        return []

    seen: set[tuple[str, int, int, str]] = set()
    out: list[SqlfluffViolation] = []
    for f in data:
        if not isinstance(f, dict):
            continue
        fp_raw = f.get("filepath") or f.get("file_path") or ""
        fp = Path(fp_raw)
        for v in f.get("violations") or []:
            if not isinstance(v, dict):
                continue
            line_raw = v.get("start_line_no") or v.get("line_no") or 1
            col_raw = v.get("start_line_pos") or v.get("line_pos") or 1
            try:
                line = max(int(line_raw), 1)
                col = max(int(col_raw), 1)
            except (TypeError, ValueError):
                line, col = 1, 1
            code = str(v.get("code") or "").strip() or "UNKNOWN"
            name = str(v.get("name") or "").strip()
            description = str(v.get("description") or "").strip()
            is_warning = bool(v.get("warning", False))

            key = (str(fp), line, col, code)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SqlfluffViolation(
                    file_path=fp,
                    line=line,
                    column=col,
                    code=code,
                    name=name,
                    description=description,
                    is_warning=is_warning,
                )
            )
    return out
