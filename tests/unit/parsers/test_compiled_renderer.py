"""SPEC-25 — unit tests for CompiledRenderer.

Covers:
* Compiled file present → render_mode=COMPILED, compiled_path populated.
* Compiled file absent → falls back to MOCK rendering (render_mode=MOCK).
* is_available reports accurate coverage ratios.
* Explicit compiled_dir override is honored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_coverage.core import RenderMode
from dbt_coverage.parsers import CompiledRenderer
from dbt_coverage.scanners import IndexedFile, ModelEntry, ProjectIndex


def _make_indexed(path: Path, content: str) -> IndexedFile:
    return IndexedFile(
        path=path,
        absolute_path=path,
        content=content,
        source_hash="abc",
    )


def _make_project(tmp: Path, sources: dict[str, str]) -> ProjectIndex:
    """Write source models under ``<tmp>/models/`` and build an index."""
    models_dir = tmp / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    models: dict[str, ModelEntry] = {}
    for rel_name, sql in sources.items():
        abs_path = tmp / rel_name
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(sql, encoding="utf-8")
        entry = ModelEntry(
            node_id=f"model.proj.{abs_path.stem}",
            name=abs_path.stem,
            sql_file=IndexedFile(
                path=Path(rel_name),
                absolute_path=abs_path,
                content=sql,
                source_hash="h",
            ),
            yml_meta=None,
        )
        models[entry.node_id] = entry
    return ProjectIndex(project_root=tmp, project_name="proj", models=models)


def _write_compiled(tmp: Path, rel: str, content: str) -> Path:
    p = tmp / "target" / "compiled" / "proj" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_compiled_renderer_hits_compiled_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path, {"models/stg_foo.sql": "select {{ ref('x') }}"})
    compiled_path = _write_compiled(
        tmp_path, "models/stg_foo.sql", "select * from dev.x"
    )

    renderer = CompiledRenderer(project, tmp_path, "proj")
    entry = next(iter(project.models.values()))

    node = renderer.render(entry.sql_file, entry.node_id)

    assert node.render_mode == RenderMode.COMPILED
    assert node.render_uncertain is False
    assert node.compiled_path == compiled_path
    assert node.rendered_sql == "select * from dev.x"
    # Identity line map.
    assert node.source_line_map[1] == 1


def test_compiled_renderer_falls_back_to_mock_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, {"models/stg_foo.sql": "select 1 as x"})
    (tmp_path / "target" / "compiled" / "proj").mkdir(parents=True)

    renderer = CompiledRenderer(project, tmp_path, "proj")
    entry = next(iter(project.models.values()))

    node = renderer.render(entry.sql_file, entry.node_id)

    assert node.render_mode == RenderMode.MOCK
    assert node.compiled_path is None


def test_is_available_reports_coverage_ratio(tmp_path: Path) -> None:
    project = _make_project(
        tmp_path,
        {
            "models/a.sql": "select 1",
            "models/b.sql": "select 2",
        },
    )
    _write_compiled(tmp_path, "models/a.sql", "select 1")

    available, ratio = CompiledRenderer.is_available(
        tmp_path, "proj", project_index=project
    )
    assert available is True
    assert ratio == pytest.approx(0.5)


def test_is_available_false_when_dir_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, {"models/a.sql": "select 1"})
    available, ratio = CompiledRenderer.is_available(
        tmp_path, "proj", project_index=project
    )
    assert available is False
    assert ratio == 0.0


def test_compiled_renderer_explicit_dir_override(tmp_path: Path) -> None:
    project = _make_project(tmp_path, {"models/x.sql": "select 1"})
    custom = tmp_path / "custom_compiled"
    (custom / "models").mkdir(parents=True)
    (custom / "models" / "x.sql").write_text("select * from raw.x", encoding="utf-8")

    renderer = CompiledRenderer(
        project, tmp_path, "proj", compiled_dir=custom
    )
    entry = next(iter(project.models.values()))
    node = renderer.render(entry.sql_file, entry.node_id)

    assert node.render_mode == RenderMode.COMPILED
    assert node.rendered_sql == "select * from raw.x"
