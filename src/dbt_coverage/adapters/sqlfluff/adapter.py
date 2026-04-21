"""SPEC-24 — SqlfluffAdapter.

Supports:
  * ``read`` mode: parse pre-existing ``sqlfluff lint --format json`` output.
  * ``run``  mode: invoke ``sqlfluff lint`` as a subprocess when available.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dbt_coverage.adapters.base import Adapter, AdapterConfig, AdapterResult
from dbt_coverage.adapters.errors import AdapterNotRunnableError
from dbt_coverage.core import AdapterInvocation, AdapterMode

from .mapper import build_severity_map, violation_to_finding
from .parser import parse_sqlfluff_json

_LOG = logging.getLogger(__name__)


class SqlfluffAdapter(Adapter):
    name: str = "sqlfluff"
    display_name: str = "SQLFluff"
    output_kinds: tuple[str, ...] = ("findings",)
    default_report_path: Path | None = Path(".dbtcov/sqlfluff.json")
    default_mode = AdapterMode.AUTO

    def __init__(self) -> None:
        self._tool_version: str | None = None

    def discover(self, project_root: Path, cfg: AdapterConfig) -> Path | None:
        override = cfg.report or (cfg.params or {}).get("report")
        candidates = []
        if override:
            p = Path(override)
            candidates.append(p if p.is_absolute() else project_root / p)
        candidates.extend(
            project_root / rel
            for rel in (
                ".dbtcov/sqlfluff.json",
                "target/sqlfluff.json",
                "sqlfluff.json",
            )
        )
        for c in candidates:
            if c.exists():
                return c
        return None

    def is_runnable(self) -> bool:
        return shutil.which("sqlfluff") is not None

    def run(self, project_root: Path, cfg: AdapterConfig) -> Path:
        if not self.is_runnable():
            raise AdapterNotRunnableError("sqlfluff not found on PATH")
        params = cfg.params or {}
        dialect = str(params.get("dialect", "snowflake"))
        paths_raw: Any = params.get("paths") or ["models/"]
        paths = [str(p) for p in paths_raw]
        out_path = project_root / ".dbtcov" / "sqlfluff.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        argv = ["sqlfluff", "lint", "--dialect", dialect, "--format", "json", *paths]
        if cfg.argv:
            argv = ["sqlfluff", *cfg.argv] if cfg.argv[0] != "sqlfluff" else list(cfg.argv)

        _LOG.info("Running %s", " ".join(argv))
        try:
            proc = subprocess.run(
                argv,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=cfg.timeout_seconds,
                check=False,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except FileNotFoundError as e:
            raise AdapterNotRunnableError(str(e)) from e

        # sqlfluff exits 1 when violations exist — that's still a valid report.
        stdout = proc.stdout or ""
        if not stdout.strip() and proc.returncode not in (0, 1):
            raise RuntimeError(
                f"sqlfluff exited {proc.returncode}: {proc.stderr.strip() or 'no output'}"
            )
        out_path.write_text(stdout, encoding="utf-8")
        self._tool_version = self._probe_version()
        return out_path

    def read(self, report_path: Path, cfg: AdapterConfig) -> AdapterResult:
        if not report_path.exists():
            return AdapterResult(
                adapter=self.name,
                invocation=AdapterInvocation(
                    adapter=self.name,
                    mode=cfg.mode,
                    report_path=report_path,
                    status="read_failed",
                    message=f"sqlfluff report not found at {report_path}",
                ),
            )
        try:
            text = report_path.read_text(encoding="utf-8")
            violations = parse_sqlfluff_json(text)
        except Exception as e:
            return AdapterResult(
                adapter=self.name,
                invocation=AdapterInvocation(
                    adapter=self.name,
                    mode=cfg.mode,
                    report_path=report_path,
                    status="read_failed",
                    message=f"sqlfluff JSON parse failed: {e}",
                ),
            )

        severity_map = build_severity_map((cfg.params or {}).get("severity_map"))
        project_root = _infer_project_root(report_path)

        findings = []
        for v in violations:
            f = violation_to_finding(v, severity_map, project_root=project_root)
            if f is not None:
                findings.append(f)

        if self._tool_version is None:
            self._tool_version = self._probe_version()

        return AdapterResult(
            adapter=self.name,
            findings=findings,
            invocation=AdapterInvocation(
                adapter=self.name,
                mode=cfg.mode,
                tool_version=self._tool_version,
                report_path=report_path,
                status="ok",
            ),
        )

    def tool_version(self) -> str | None:
        return self._tool_version

    @staticmethod
    def _probe_version() -> str | None:
        try:
            proc = subprocess.run(
                ["sqlfluff", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            # e.g. "sqlfluff, version 3.0.7" -> take last token
            return out.split()[-1] if out else None
        except Exception:
            return None


def _infer_project_root(report_path: Path) -> Path:
    """Heuristic: strip ``.dbtcov`` / ``target`` suffix from report_path.parent."""
    parent = report_path.parent
    if parent.name in (".dbtcov", "target"):
        return parent.parent
    return parent
