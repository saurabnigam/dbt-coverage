"""SPEC-10a §4.2 — canonical JSON reporter.

Skip detail honours ``ReportsConfig.json`` → ``skip_detail`` (SPEC-33 §6):
* ``summary``    — drop both aggregated and per-pair records.
* ``aggregated`` — keep ``check_skips_aggregated`` but drop ``check_skips``.
* ``per_pair``   — keep everything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dbt_coverage.core import ScanResult


class JSONReporter:
    name = "json"
    default_filename = "findings.json"

    def __init__(self, skip_detail: str = "aggregated") -> None:
        self.skip_detail = (skip_detail or "aggregated").lower()

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        # We load-then-dump so we can prune without mutating the live object.
        data = json.loads(result.model_dump_json())
        _prune_skips(data, self.skip_detail)
        payload = json.dumps(data, indent=2, sort_keys=False)
        if out is None:
            sys.stdout.write(payload)
            sys.stdout.write("\n")
            return
        out = _resolve_path(out, self.default_filename)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")


def _prune_skips(data: dict, detail: str) -> None:
    if detail == "per_pair":
        return
    if detail == "aggregated":
        data["check_skips"] = []
        return
    # summary
    data["check_skips"] = []
    data["check_skips_aggregated"] = []


def _resolve_path(out: Path, default_name: str) -> Path:
    if out.exists() and out.is_dir():
        return out / default_name
    if out.suffix == "":
        return out / default_name
    return out
