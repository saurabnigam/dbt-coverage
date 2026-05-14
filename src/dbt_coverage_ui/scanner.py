"""Background scan runner — invokes `dbtcov scan` in a subprocess thread."""

from __future__ import annotations

import json
import logging
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

_LOG = logging.getLogger(__name__)


def trigger_scan(
    *,
    project_id: str,
    project_path: Path,
    artifacts_dir: Path,
    render_mode: str,
    run_id: str,
    on_finish,
) -> None:
    """Spawn a background thread that runs `dbtcov scan` and updates the store."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(
        target=_run,
        kwargs=dict(
            project_id=project_id,
            project_path=project_path,
            artifacts_dir=artifacts_dir,
            render_mode=render_mode,
            run_id=run_id,
            on_finish=on_finish,
        ),
        daemon=True,
    )
    t.start()


def _run(
    *,
    project_id: str,
    project_path: Path,
    artifacts_dir: Path,
    render_mode: str,
    run_id: str,
    on_finish,
) -> None:
    started = time.perf_counter()
    log_path = artifacts_dir / "console.txt"
    cmd = [
        sys.executable,
        "-m",
        "dbt_coverage.cli.main",
        "scan",
        "--path",
        str(project_path),
        "--render-mode",
        render_mode,
        "--format",
        "json",
        "--format",
        "sarif",
        "--out",
        str(artifacts_dir),
    ]
    summary: dict = {"project_id": project_id, "status": "failed"}
    try:
        with log_path.open("w", encoding="utf-8") as log_f:
            proc = subprocess.run(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=600,
            )
        if proc.returncode not in (0, 1):  # 0=clean, 1=gate-fail (still produces artifacts)
            summary["error_message"] = (
                f"dbtcov scan exited with code {proc.returncode}; see console.txt"
            )
        else:
            result = _summarize(artifacts_dir)
            summary.update(result)
            if "error_message" not in result:
                summary["status"] = "success"
    except subprocess.TimeoutExpired:
        summary["error_message"] = "Scan timed out after 600s"
    except Exception as e:  # noqa: BLE001
        summary["error_message"] = f"Scan failed: {e!r}"
        _LOG.exception("Scan run %s failed", run_id)
    finally:
        summary["duration_ms"] = int((time.perf_counter() - started) * 1000)
        try:
            on_finish(run_id, summary)
        except Exception:  # noqa: BLE001
            _LOG.exception("on_finish callback failed for run %s", run_id)


def _summarize(artifacts_dir: Path) -> dict:
    """Pull headline metrics out of findings.json."""
    findings_path = artifacts_dir / "findings.json"
    if not findings_path.exists():
        return {"error_message": "findings.json missing after scan"}
    data = json.loads(findings_path.read_text(encoding="utf-8"))
    findings = data.get("findings", [])
    coverage = {c["dimension"]: c for c in data.get("coverage", [])}
    models = data.get("model_summaries", [])
    render = data.get("render_stats", {})
    sev = Counter(f["severity"] for f in findings)
    scores = [m["score"] for m in models]
    out: dict = {
        "render_mode": _dominant_render_mode(render),
        "findings_total": len(findings),
        "findings_critical": sev.get("CRITICAL", 0) + sev.get("BLOCKER", 0),
        "findings_major": sev.get("MAJOR", 0),
        "findings_minor": sev.get("MINOR", 0) + sev.get("INFO", 0),
        "models_total": len(models),
        "models_at_risk": sum(1 for s in scores if s < 70),
        "parse_failed": render.get("parse_failed", 0),
        "score_mean": round(statistics.mean(scores), 2) if scores else None,
        "score_median": round(statistics.median(scores), 2) if scores else None,
    }
    for dim in ("test", "doc", "test_unit", "test_meaningful", "complexity",
                "column_test", "column_test_meaningful", "test_unit_weighted_cc"):
        c = coverage.get(dim)
        if c is not None:
            out[f"coverage_{dim}"] = round(c.get("ratio", 0.0), 4)
    return out


def _dominant_render_mode(render: dict) -> str:
    counts = {
        "COMPILED": render.get("rendered_compiled", 0),
        "MOCK": render.get("rendered_mock", 0),
        "PARTIAL": render.get("rendered_partial", 0),
    }
    if not any(counts.values()):
        return "UNKNOWN"
    return max(counts.items(), key=lambda kv: kv[1])[0]
