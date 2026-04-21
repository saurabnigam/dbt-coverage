"""SPEC-01 §4.7 — stable fingerprint for baseline-diffing findings."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_fingerprint(rule_id: str, file_path: str | Path, code_context: str) -> str:
    """
    Stable hash for baseline-diffing Findings across scans.

    Intentionally excludes line/column numbers so cosmetic reformatting
    (added import, newline above) does not churn fingerprints.

    ``code_context`` is the normalized SQL snippet triggering the finding.
    Callers are responsible for normalization (see normalization.extract_code_context).
    """
    h = hashlib.sha256()
    h.update(rule_id.encode())
    h.update(b"\0")
    h.update(str(file_path).encode())
    h.update(b"\0")
    h.update(code_context.encode())
    return h.hexdigest()[:16]
