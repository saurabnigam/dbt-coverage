"""SPEC-25 — COMPILED render mode.

Reads pre-compiled SQL from ``target/compiled/<project_name>/...`` (the
artifact dbt writes during ``dbt compile``) and produces ``ParsedNode``
objects ready for :class:`SqlParser`.

Goals:
* Drastically lower the ``render_uncertain`` / ``parse_failed`` count on
  projects with heavy macro / cross-package usage — the compiled SQL has
  already had every ref, source, macro, and var resolved by dbt itself.
* Never raise — a missing compiled file silently falls back to MOCK for
  that one node, preserving SPEC-01 §3 principle #3 (graceful degradation).
"""

from __future__ import annotations

import logging
from pathlib import Path

from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.scanners import IndexedFile, ProjectIndex

from .jinja_renderer import JinjaRenderer

_LOG = logging.getLogger(__name__)


class CompiledRenderer:
    """Reads ``target/compiled/<project>/<model>.sql`` and returns ParsedNodes."""

    def __init__(
        self,
        project_index: ProjectIndex,
        project_root: Path,
        project_name: str,
        compiled_dir: Path | None = None,
        fallback: JinjaRenderer | None = None,
    ) -> None:
        self.project_index = project_index
        self.project_root = Path(project_root).resolve()
        self.project_name = project_name
        self.compiled_dir = _resolve_compiled_dir(
            self.project_root, project_name, compiled_dir
        )
        self.fallback = fallback or JinjaRenderer(project_index)

    # ------------------------------------------------------------------ API

    def render(self, file: IndexedFile, node_id: str | None = None) -> ParsedNode:
        compiled = self.resolve_compiled_path(file.path)
        if compiled is None:
            # Degrade to MOCK for this one file; note render_mode stays MOCK,
            # compiled_path=None so downstream can see the distinction.
            return self.fallback.render(file, node_id)

        try:
            content = compiled.read_text(encoding="utf-8")
        except OSError as e:
            _LOG.debug("compiled read failed for %s: %s", compiled, e)
            return self.fallback.render(file, node_id)

        n_lines = content.count("\n") + 1
        identity = {i: i for i in range(1, n_lines + 1)}

        return ParsedNode(
            file_path=file.path,
            node_id=node_id,
            source_sql=file.content,
            rendered_sql=content,
            ast=None,
            line_map=identity,
            config={},
            refs=[],
            sources=[],
            macros_used=[],
            render_mode=RenderMode.COMPILED,
            render_uncertain=False,
            parse_success=True,
            parse_error=None,
            compiled_path=compiled,
            source_line_map=dict(identity),
        )

    def render_all(
        self, files: list[IndexedFile], node_ids: list[str | None] | None = None
    ) -> list[ParsedNode]:
        if node_ids is None:
            node_ids = [None] * len(files)
        return [self.render(f, nid) for f, nid in zip(files, node_ids, strict=False)]

    # -------------------------------------------------------------- helpers

    def resolve_compiled_path(self, source_file: Path) -> Path | None:
        """Maps a project-root-relative source path to the compiled artifact.

        dbt places compiled SQL at ``target/compiled/<project_name>/<source_rel>``
        so we just graft the source-relative path onto ``self.compiled_dir``.
        """
        if self.compiled_dir is None:
            return None
        rel = source_file
        if source_file.is_absolute():
            try:
                rel = source_file.relative_to(self.project_root)
            except ValueError:
                return None
        candidate = self.compiled_dir / rel
        return candidate if candidate.exists() and candidate.is_file() else None

    # ---------------------------------------------------------- class-level

    @classmethod
    def is_available(
        cls,
        project_root: Path,
        project_name: str,
        project_index: ProjectIndex | None = None,
        compiled_dir: Path | None = None,
    ) -> tuple[bool, float]:
        """Return ``(exists, coverage_ratio)``.

        ``exists`` is True when the compiled dir is a directory; ``coverage_ratio``
        is ``hits/total`` over the discovered model files. If ``project_index`` is
        omitted the ratio is reported as ``1.0`` when the directory exists (we
        can't compute hit-rate without the file list).
        """
        resolved = _resolve_compiled_dir(
            Path(project_root).resolve(), project_name, compiled_dir
        )
        if resolved is None:
            return False, 0.0
        if project_index is None:
            return True, 1.0
        total = len(project_index.models)
        if total == 0:
            return True, 0.0
        hits = 0
        for entry in project_index.models.values():
            rel = entry.sql_file.path
            if rel.is_absolute():
                try:
                    rel = rel.relative_to(Path(project_root).resolve())
                except ValueError:
                    continue
            if (resolved / rel).is_file():
                hits += 1
        return True, hits / total


# ------------------------------------------------------------------ helpers


def _resolve_compiled_dir(
    project_root: Path,
    project_name: str,
    explicit: Path | None,
) -> Path | None:
    """Try the explicit override first, then dbt's conventional locations.

    Resolution order (first hit wins):
      1. explicit arg (abs or project-relative)
      2. target/compiled/<project_name>
      3. target/compiled
    """
    candidates: list[Path] = []
    if explicit is not None:
        p = Path(explicit)
        candidates.append(p if p.is_absolute() else project_root / p)
    candidates.append(project_root / "target" / "compiled" / project_name)
    candidates.append(project_root / "target" / "compiled")
    for c in candidates:
        if c.is_dir():
            return c
    return None
