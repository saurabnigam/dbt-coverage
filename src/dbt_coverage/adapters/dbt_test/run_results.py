"""SPEC-23 §5.2 / §7.2 — run_results.json loader."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from dbt_coverage.adapters.errors import UnsupportedSchemaError
from dbt_coverage.core import TestStatus

_SCHEMA_RE = re.compile(r"/v(\d+)(?:\.json)?/?$")


@dataclass(frozen=True)
class RunResultEntry:
    unique_id: str
    status: TestStatus
    message: str | None
    execution_time: float


@dataclass(frozen=True)
class RunResultsIndex:
    dbt_version: str | None
    schema_version: int
    results_by_unique_id: dict[str, RunResultEntry]


def parse_run_results(path: Path, *, treat_warn_as_pass: bool = True) -> RunResultsIndex:
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    schema = _extract_schema_version(raw)
    if schema < 4:
        raise UnsupportedSchemaError(schema, tool="dbt run_results")

    meta = raw.get("metadata") or {}
    dbt_version = meta.get("dbt_version")

    out: dict[str, RunResultEntry] = {}
    for r in raw.get("results") or []:
        uid = r.get("unique_id")
        if not isinstance(uid, str):
            continue
        if not (uid.startswith("test.") or uid.startswith("unit_test.")):
            continue
        status = _map_status(r.get("status"), treat_warn_as_pass=treat_warn_as_pass)
        msg = r.get("message")
        try:
            etime = float(r.get("execution_time") or 0.0)
        except (TypeError, ValueError):
            etime = 0.0
        out[uid] = RunResultEntry(
            unique_id=uid,
            status=status,
            message=str(msg) if msg is not None else None,
            execution_time=etime,
        )

    return RunResultsIndex(
        dbt_version=dbt_version,
        schema_version=schema,
        results_by_unique_id=out,
    )


def _extract_schema_version(raw: dict) -> int:
    meta = raw.get("metadata") or {}
    ver = str(meta.get("dbt_schema_version") or "")
    m = _SCHEMA_RE.search(ver)
    if m:
        return int(m.group(1))
    return 0


def _map_status(status: object, *, treat_warn_as_pass: bool = True) -> TestStatus:
    """Map a run_results.json ``status`` string to our ``TestStatus`` enum.

    dbt's schema has drifted across versions: v4/v5 use ``pass``/``fail`` for
    tests, while v6+ (dbt 1.8+) sometimes serialises successful tests as
    ``success`` (the unified RunStatus label). We accept both so coverage
    numbers are correct on real-world projects regardless of dbt version.
    """
    s = str(status or "").strip().lower()
    if s in ("pass", "success"):
        return TestStatus.PASS
    if s == "fail":
        return TestStatus.FAIL
    if s == "error":
        return TestStatus.ERROR
    if s == "skipped":
        return TestStatus.SKIPPED
    if s == "warn":
        return TestStatus.PASS if treat_warn_as_pass else TestStatus.FAIL
    return TestStatus.UNKNOWN
