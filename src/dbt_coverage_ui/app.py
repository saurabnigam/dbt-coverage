"""FastAPI application for the dbtcov UI."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import Store
from .metadata import (
    COLUMN_TOOLTIPS,
    CONFIG_FIELDS,
    DIMENSION_DESCRIPTIONS,
    RULE_DESCRIPTIONS,
)
from .scanner import trigger_scan

_LOG = logging.getLogger(__name__)


def _data_root() -> Path:
    override = os.environ.get("DBTCOV_UI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".dbtcov-ui"


def _recover_stale_runs(store: Store) -> None:
    """On startup, mark any 'running' runs as failed/success depending on whether artifacts exist."""
    with store._conn() as c:  # noqa: SLF001
        stale = c.execute(
            "SELECT id, project_id, artifacts_dir FROM runs WHERE status = 'running'"
        ).fetchall()
    for row in stale:
        run_id, project_id, artifacts_dir = row["id"], row["project_id"], row["artifacts_dir"]
        if not artifacts_dir:
            _mark_failed(store, run_id, project_id, "Server restarted before scan completed")
            continue
        findings = Path(artifacts_dir) / "findings.json"
        if findings.exists():
            from .scanner import _summarize
            try:
                summary = _summarize(Path(artifacts_dir))
                summary["project_id"] = project_id
                summary["status"] = "success"
                summary.setdefault("duration_ms", 0)
                store.finish_run(run_id, summary)
                _LOG.info("Recovered stale run %s → success", run_id)
            except Exception:  # noqa: BLE001
                _mark_failed(store, run_id, project_id, "Recovery parse failed")
        else:
            _mark_failed(store, run_id, project_id, "Server restarted; artifacts incomplete")


def _mark_failed(store: Store, run_id: str, project_id: str, msg: str) -> None:
    store.finish_run(run_id, {"project_id": project_id, "status": "failed", "error_message": msg})


def _artifacts_dir(root: Path, project_id: str, run_id: str) -> Path:
    return root / "runs" / project_id / run_id


# ---- request schemas -------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str
    path: str


class ScanRequest(BaseModel):
    render_mode: str = "auto"


# ---- factory ---------------------------------------------------------------


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or _data_root()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "dbtcov-ui.sqlite")
    _recover_stale_runs(store)

    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="dbtcov UI", version="0.1.0")

    # ---- root / static --------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((static_dir / "index.html").read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ---- metadata -------------------------------------------------------

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "rules": RULE_DESCRIPTIONS,
            "dimensions": DIMENSION_DESCRIPTIONS,
            "config_fields": CONFIG_FIELDS,
            "column_tooltips": COLUMN_TOOLTIPS,
        }

    # ---- projects -------------------------------------------------------

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return store.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate) -> dict:
        path = Path(body.path).expanduser().resolve()
        if not path.exists():
            raise HTTPException(400, f"Path does not exist: {path}")
        if not (path / "dbt_project.yml").exists() and not any(
            (path / "config" / "dbt_project.yml").exists() for _ in [0]
        ):
            # Soft warning — user might still register projects with non-standard layouts
            _LOG.info("Path %s has no dbt_project.yml at root; registering anyway", path)
        if store.get_project_by_name(body.name):
            raise HTTPException(409, f"Project named {body.name!r} already exists.")
        return store.create_project(body.name, str(path))

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        proj = store.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "Project not found")
        proj["runs"] = store.list_runs(project_id)
        proj["config_path"] = str(Path(proj["path"]) / "dbtcov.yml")
        return proj

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str) -> None:
        if store.get_project(project_id) is None:
            raise HTTPException(404, "Project not found")
        store.delete_project(project_id)

    # ---- scan -----------------------------------------------------------

    @app.post("/api/projects/{project_id}/scan", status_code=202)
    def scan(project_id: str, body: ScanRequest) -> dict:
        proj = store.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "Project not found")
        artifacts_dir = _artifacts_dir(root, project_id, "_pending")
        run_id = store.create_run(project_id, str(artifacts_dir))
        artifacts_dir = _artifacts_dir(root, project_id, run_id)

        # Re-write the artifacts_dir on the run to the final path
        with store._conn() as c:  # noqa: SLF001
            c.execute(
                "UPDATE runs SET artifacts_dir = ? WHERE id = ?",
                (str(artifacts_dir), run_id),
            )

        trigger_scan(
            project_id=project_id,
            project_path=Path(proj["path"]),
            artifacts_dir=artifacts_dir,
            render_mode=body.render_mode,
            run_id=run_id,
            on_finish=store.finish_run,
        )
        return {"run_id": run_id, "status": "running"}

    # ---- runs -----------------------------------------------------------

    @app.get("/api/projects/{project_id}/runs")
    def list_runs(project_id: str) -> list[dict]:
        if store.get_project(project_id) is None:
            raise HTTPException(404, "Project not found")
        return store.list_runs(project_id)

    @app.get("/api/projects/{project_id}/runs/{run_id}")
    def get_run(project_id: str, run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None or run["project_id"] != project_id:
            raise HTTPException(404, "Run not found")
        return run

    @app.get("/api/projects/{project_id}/runs/{run_id}/coverage")
    def coverage_payload(project_id: str, run_id: str) -> Any:
        return _read_artifact(store, project_id, run_id, "coverage.json")

    @app.get("/api/projects/{project_id}/runs/{run_id}/findings")
    def findings_payload(
        project_id: str,
        run_id: str,
        rule_id: str | None = None,
        severity: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        data = _read_artifact(store, project_id, run_id, "findings.json")
        findings = data.get("findings", []) if isinstance(data, dict) else []
        if rule_id:
            findings = [f for f in findings if f.get("rule_id") == rule_id]
        if severity:
            findings = [f for f in findings if f.get("severity") == severity]
        total = len(findings)
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "findings": findings[offset : offset + limit],
        }

    @app.get("/api/projects/{project_id}/runs/{run_id}/models")
    def models_payload(project_id: str, run_id: str) -> list[dict]:
        data = _read_artifact(store, project_id, run_id, "findings.json")
        if not isinstance(data, dict):
            return []
        return data.get("model_summaries", [])

    @app.get("/api/projects/{project_id}/runs/{run_id}/per-model-coverage")
    def per_model_coverage(project_id: str, run_id: str) -> dict:
        """Return one row per model with each coverage dimension's [covered,total]."""
        cov = _read_artifact(store, project_id, run_id, "coverage.json")
        findings = _read_artifact(store, project_id, run_id, "findings.json")
        model_summaries = findings.get("model_summaries", []) if isinstance(findings, dict) else []
        # node_id -> summary
        by_node = {m["node_id"]: m for m in model_summaries}
        # collect dimensions and per-node coverage
        dims: list[str] = []
        per_node_dims: dict[str, dict[str, list[int]]] = {}  # node_id -> dim -> [c,t]
        for entry in cov.get("coverage", []) if isinstance(cov, dict) else []:
            dim = entry.get("dimension")
            if not dim:
                continue
            dims.append(dim)
            for nid, ct in (entry.get("per_node") or {}).items():
                per_node_dims.setdefault(nid, {})[dim] = list(ct) if isinstance(ct, (list, tuple)) else [0, 0]
        # build rows: union of nodes from summaries + coverage
        all_nodes = set(by_node.keys()) | set(per_node_dims.keys())
        rows = []
        for nid in all_nodes:
            summary = by_node.get(nid, {})
            rows.append({
                "node_id": nid,
                "name": summary.get("name") or nid.rsplit(".", 1)[-1],
                "file_path": summary.get("file_path", ""),
                "score": summary.get("score"),
                "score_breakdown": summary.get("score_breakdown", {}),
                "parse_success": summary.get("parse_success", True),
                "render_uncertain": summary.get("render_uncertain", False),
                "test_covered": summary.get("test_covered", False),
                "doc_ratio": summary.get("doc_ratio", 0.0),
                "tier1_rules": summary.get("tier1_rules", []),
                "tier2_rules": summary.get("tier2_rules", []),
                "data_test_count": summary.get("data_test_count", 0),
                "unit_test_count": summary.get("unit_test_count", 0),
                "tests_not_run_count": summary.get("tests_not_run_count", 0),
                "waived_count": summary.get("waived_count", 0),
                "skip_count": summary.get("skip_count", 0),
                "finding_count": (len(summary.get("tier1_rules", [])) + len(summary.get("tier2_rules", []))),
                "dims": per_node_dims.get(nid, {}),
            })
        rows.sort(key=lambda r: (r["score"] if r["score"] is not None else 999, r["name"]))
        return {"dimensions": dims, "models": rows}

    @app.get("/api/projects/{project_id}/runs/{run_id}/artifact/{kind}")
    def download_artifact(project_id: str, run_id: str, kind: str) -> FileResponse:
        run = store.get_run(run_id)
        if run is None or run["project_id"] != project_id:
            raise HTTPException(404, "Run not found")
        valid = {
            "findings.json": "application/json",
            "coverage.json": "application/json",
            "findings.sarif": "application/sarif+json",
            "console.txt": "text/plain",
        }
        if kind not in valid:
            raise HTTPException(400, f"Unknown artifact: {kind}")
        path = Path(run["artifacts_dir"]) / kind
        if not path.exists():
            raise HTTPException(404, f"{kind} not produced by this run")
        return FileResponse(path, media_type=valid[kind], filename=f"{run_id}-{kind}")

    # ---- trend ----------------------------------------------------------

    @app.get("/api/projects/{project_id}/trend")
    def trend(project_id: str) -> list[dict]:
        if store.get_project(project_id) is None:
            raise HTTPException(404, "Project not found")
        runs = store.list_runs(project_id, limit=200)
        # ascending
        runs = sorted(runs, key=lambda r: r["started_at"])
        return [
            {
                "run_id": r["id"],
                "started_at": r["started_at"],
                "score_mean": r["score_mean"],
                "score_median": r["score_median"],
                "findings_total": r["findings_total"],
                "findings_critical": r["findings_critical"],
                "coverage_test": r["coverage_test"],
                "coverage_doc": r["coverage_doc"],
                "coverage_test_unit": r["coverage_test_unit"],
                "models_at_risk": r["models_at_risk"],
                "render_mode": r["render_mode"],
                "status": r["status"],
            }
            for r in runs
            if r["status"] == "success"
        ]

    # ---- config editor --------------------------------------------------

    @app.get("/api/projects/{project_id}/config")
    def get_config(project_id: str) -> dict:
        proj = store.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "Project not found")
        cfg_path = Path(proj["path"]) / "dbtcov.yml"
        if not cfg_path.exists():
            return {"exists": False, "yaml": "", "path": str(cfg_path)}
        return {
            "exists": True,
            "yaml": cfg_path.read_text(encoding="utf-8"),
            "path": str(cfg_path),
        }

    @app.put("/api/projects/{project_id}/config")
    def put_config(project_id: str, body: dict) -> dict:
        proj = store.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "Project not found")
        yaml_text = body.get("yaml", "")
        if not isinstance(yaml_text, str):
            raise HTTPException(400, "yaml field must be a string")
        # Validate YAML syntax
        try:
            from ruamel.yaml import YAML

            YAML(typ="safe").load(yaml_text)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Invalid YAML: {e}")
        cfg_path = Path(proj["path"]) / "dbtcov.yml"
        cfg_path.write_text(yaml_text, encoding="utf-8")
        return {"saved": True, "path": str(cfg_path)}

    @app.get("/api/config-template")
    def config_template() -> dict:
        from importlib import resources

        template = resources.files("dbt_coverage.templates").joinpath("dbtcov.yml.template")
        return {"yaml": template.read_text(encoding="utf-8")}

    return app


# ---- helpers ---------------------------------------------------------------


def _read_artifact(store: Store, project_id: str, run_id: str, name: str) -> Any:
    run = store.get_run(run_id)
    if run is None or run["project_id"] != project_id:
        raise HTTPException(404, "Run not found")
    path = Path(run["artifacts_dir"]) / name
    if not path.exists():
        raise HTTPException(404, f"{name} not produced by this run")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Corrupt {name}: {e}")
