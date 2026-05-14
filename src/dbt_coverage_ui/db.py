"""SQLite persistence for dbtcov UI: projects + runs."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_run_id TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,         -- running | success | failed
    started_at TEXT NOT NULL,
    finished_at TEXT,
    render_mode TEXT,
    score_mean REAL,
    score_median REAL,
    findings_total INTEGER,
    findings_critical INTEGER,
    findings_major INTEGER,
    findings_minor INTEGER,
    models_total INTEGER,
    models_at_risk INTEGER,
    parse_failed INTEGER,
    coverage_test REAL,
    coverage_doc REAL,
    coverage_test_unit REAL,
    coverage_test_meaningful REAL,
    coverage_complexity REAL,
    coverage_column_test REAL,
    coverage_column_test_meaningful REAL,
    coverage_test_unit_weighted_cc REAL,
    duration_ms INTEGER,
    error_message TEXT,
    artifacts_dir TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, started_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Thin wrapper over a single sqlite file."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()

    # ---- projects ----------------------------------------------------------

    def list_projects(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT p.*, r.score_mean AS last_score, r.findings_total AS last_findings,
                       r.started_at AS last_run_started, r.status AS last_run_status
                FROM projects p
                LEFT JOIN runs r ON r.id = p.last_run_id
                ORDER BY p.name
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return dict(row) if row else None

    def get_project_by_name(self, name: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    def create_project(self, name: str, path: str) -> dict:
        pid = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO projects (id, name, path, created_at) VALUES (?, ?, ?, ?)",
                (pid, name, path, _now()),
            )
        return self.get_project(pid)  # type: ignore[return-value]

    def delete_project(self, project_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ---- runs --------------------------------------------------------------

    def create_run(self, project_id: str, artifacts_dir: str) -> str:
        rid = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO runs (id, project_id, status, started_at, artifacts_dir)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (rid, project_id, _now(), artifacts_dir),
            )
        return rid

    def finish_run(self, run_id: str, summary: dict) -> None:
        cols = (
            "status",
            "finished_at",
            "render_mode",
            "score_mean",
            "score_median",
            "findings_total",
            "findings_critical",
            "findings_major",
            "findings_minor",
            "models_total",
            "models_at_risk",
            "parse_failed",
            "coverage_test",
            "coverage_doc",
            "coverage_test_unit",
            "coverage_test_meaningful",
            "coverage_complexity",
            "duration_ms",
            "error_message",
        )
        values = [summary.get(c) for c in cols]
        if not summary.get("finished_at"):
            values[1] = _now()
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {set_clause} WHERE id = ?", [*values, run_id])
            if summary.get("status") == "success":
                c.execute(
                    "UPDATE projects SET last_run_id = ? WHERE id = ?",
                    (run_id, summary["project_id"]),
                )

    def list_runs(self, project_id: str, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None
