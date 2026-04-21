"""SPEC-05 — MOCK-mode Jinja renderer producing parsable SQL + line maps."""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, StrictUndefined, TemplateError, UndefinedError

from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.scanners import IndexedFile, ProjectIndex

from .line_map import extract_line_map, inject_line_markers
from .macro_indexer import MacroRegistry, index_macros
from .mock_context import (
    AdapterDispatchUnsupported,
    CapturedConfig,
    build_mock_context,
)

_LOG = logging.getLogger(__name__)


class JinjaRenderer:
    """Renders dbt-templated SQL to parsable SQL without executing dbt."""

    def __init__(
        self,
        project_index: ProjectIndex,
        mode: RenderMode = RenderMode.MOCK,
        cache_dir: Path | None = None,
        adapter_name: str | None = None,
    ) -> None:
        if mode is not RenderMode.MOCK:
            _LOG.warning("Only MOCK mode is implemented in phase 1; falling back from %s", mode)
        self.mode = RenderMode.MOCK
        self.project_index = project_index
        self.adapter_name = adapter_name
        self.macro_registry: MacroRegistry = index_macros(project_index)

        # StrictUndefined so any unknown macro/variable raises UndefinedError,
        # which the render loop catches and turns into render_uncertain=True.
        self._env = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=True,
        )

    # ----------------------------------------------------------- public API

    def render(self, file: IndexedFile, node_id: str | None = None) -> ParsedNode:
        """Render one file; never raises."""
        markers_sql = inject_line_markers(file.content)

        captured_config = CapturedConfig()
        captured_refs: list[str] = []
        captured_sources: list[tuple[str, str]] = []
        captured_macros: list[str] = []

        ctx = build_mock_context(
            self.macro_registry,
            captured_config,
            captured_refs,
            captured_sources,
            captured_macros,
            adapter_name=self.adapter_name,
        )

        render_uncertain = False
        rendered_with_markers: str
        try:
            template = self._env.from_string(markers_sql)
            rendered_with_markers = template.render(ctx)
        except (UndefinedError, TemplateError, AdapterDispatchUnsupported, NameError) as e:
            _LOG.debug("render uncertain for %s: %s", file.path, e)
            render_uncertain = True
            rendered_with_markers = file.content
        except RecursionError:
            _LOG.debug("render recursion error for %s", file.path)
            render_uncertain = True
            rendered_with_markers = file.content
        except Exception as e:  # last-ditch safety net
            _LOG.warning("Unexpected render error for %s: %s", file.path, e)
            render_uncertain = True
            rendered_with_markers = file.content

        if render_uncertain:
            # Identity line map against the original source.
            n = file.content.count("\n") + 1
            rendered_sql = file.content
            line_map = {i: i for i in range(1, n + 1)}
        else:
            rendered_sql, line_map = extract_line_map(rendered_with_markers)

        return ParsedNode(
            file_path=file.path,
            node_id=node_id,
            source_sql=file.content,
            rendered_sql=rendered_sql,
            ast=None,
            line_map=line_map,
            config=dict(captured_config.data),
            refs=list(dict.fromkeys(captured_refs)),
            sources=list(dict.fromkeys(captured_sources)),
            macros_used=list(dict.fromkeys(captured_macros)),
            render_mode=RenderMode.MOCK,
            render_uncertain=render_uncertain,
            parse_success=True,
            parse_error=None,
        )

    def render_all(
        self, files: list[IndexedFile], node_ids: list[str | None] | None = None
    ) -> list[ParsedNode]:
        """Sequential render (parallelism deferred; overhead dominates for small files)."""
        if node_ids is None:
            node_ids = [None] * len(files)
        return [self.render(f, nid) for f, nid in zip(files, node_ids, strict=False)]
