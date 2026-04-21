# SPEC-25 — COMPILED render mode

## 1. Problem

The MOCK renderer (SPEC-05) in [src/dbt_coverage/parsers/jinja_renderer.py](../../src/dbt_coverage/parsers/jinja_renderer.py) bails whenever a template uses an unknown macro, adapter-dispatched macro, or project-local `var()` / cross-package ref it can't simulate. On `pontus-models` this produces `render_uncertain=True` for 281 / 498 files and `parse_failed=286` — every AST-based rule silently skips ~57% of the project.

`dbt compile` pre-resolves every ref/source/macro/var and writes the resulting SQL to `target/compiled/<project>/<path>/<model>.sql`. Reading from there — instead of re-rendering — recovers the lost 57% in a single additive change.

## 2. Goals

1. Add a second renderer that reads from `target/compiled` and produces `ParsedNode` objects ready for `SqlParser`.
2. Auto-detect availability — use COMPILED when `target/compiled` covers a configurable fraction of the project, else fall back to MOCK.
3. Line numbers in findings point at the compiled file (v1); a later spec addresses source-line remapping.
4. Zero changes to existing rule APIs — rules keep seeing `ParsedNode` objects.

## 3. Non-goals

- Running `dbt compile` on behalf of the user. The adapter assumes artifacts exist.
- Source-line remapping (v2, requires either dbt's `source_map` or difflib alignment).
- Incremental caching between runs.

## 4. Contracts

### 4.1 Enum renames

`RenderMode` in [src/dbt_coverage/core/enums.py](../../src/dbt_coverage/core/enums.py) gains a `COMPILED` member. The existing `DBT` value remains as an alias that resolves to `COMPILED` for backwards compatibility. A new `AUTO` value represents *dispatcher-chosen* mode (only legal in config; the dispatcher collapses it to MOCK or COMPILED before renderers are constructed).

```python
class RenderMode(StrEnum):
    MOCK = "MOCK"
    PARTIAL = "PARTIAL"
    COMPILED = "COMPILED"
    DBT = "COMPILED"   # legacy alias — serialises as "COMPILED"
    AUTO = "AUTO"      # dispatcher-only; never appears on a ParsedNode
```

### 4.2 ParsedNode additions

[src/dbt_coverage/core/parsed_node.py](../../src/dbt_coverage/core/parsed_node.py) gains two additive fields:

```python
compiled_path: Path | None = None
source_line_map: dict[int, int] = Field(default_factory=dict)
```

- `compiled_path` is set when the node was rendered from `target/compiled/**/*.sql` — `None` for MOCK nodes.
- `source_line_map` is a forward-compatible map `compiled_line -> source_line`. V1: identity map (keys = values). V2 (future): populated from dbt's `source_map.json` or diff alignment.

### 4.3 CompiledRenderer contract

New module `src/dbt_coverage/parsers/compiled_renderer.py`:

```python
class CompiledRenderer:
    def __init__(
        self,
        project_index: ProjectIndex,
        project_root: Path,
        project_name: str,
        compiled_dir: Path | None = None,
    ) -> None: ...

    def render(self, file: IndexedFile, node_id: str | None = None) -> ParsedNode:
        """Read <compiled_dir>/<relative-path-of-file>. Returns:
          render_mode=COMPILED, render_uncertain=False, compiled_path=<...>
        when the compiled file exists, else falls back to MOCK renderer for that file.
        Never raises.
        """

    def render_all(self, files, node_ids=None) -> list[ParsedNode]: ...

    @classmethod
    def is_available(
        cls, project_root: Path, project_name: str, compiled_dir: Path | None = None
    ) -> tuple[bool, float]:
        """Returns (exists, coverage_ratio) where coverage is:
          (# models whose compiled SQL exists) / (# models discovered on disk)
        """

    def resolve_compiled_path(self, source_file: Path) -> Path | None:
        """Maps models/foo/bar.sql → <compiled_dir>/models/foo/bar.sql."""
```

### 4.4 Compiled-dir discovery

Resolution order (first hit wins):

1. Explicit `config.render.compiled_dir` (absolute or project-root-relative).
2. Explicit `--compiled-dir` CLI flag.
3. `<project_root>/target/compiled/<project_name>/` (dbt default).
4. `<project_root>/target/compiled/` (single-project layouts).

If none exists, `is_available()` returns `(False, 0.0)` and the dispatcher falls back to MOCK.

### 4.5 Mode selection

New helper in [src/dbt_coverage/cli/orchestrator.py](../../src/dbt_coverage/cli/orchestrator.py):

```python
def _select_renderer(project, project_info, config) -> Renderer:
    mode = config.render.mode
    if mode == RenderMode.MOCK:
        return JinjaRenderer(...)
    if mode == RenderMode.COMPILED:
        return CompiledRenderer(...)
    # AUTO
    ok, ratio = CompiledRenderer.is_available(project_info.root, project_info.name,
                                              compiled_dir=config.render.compiled_dir)
    if ok and ratio >= config.render.compiled_min_coverage:
        return CompiledRenderer(...)
    return JinjaRenderer(...)
```

`AUTO` default for `compiled_min_coverage` is `0.5` — require at least half the models to have compiled artifacts before switching.

### 4.6 Config additions

```yaml
render:
  mode: auto              # auto | mock | compiled (was just "mock" before)
  compiled_dir: null      # optional override, project-relative or absolute
  compiled_min_coverage: 0.5
  fallback: mock          # unused in v1; reserved
```

### 4.7 CLI flags

Added in [src/dbt_coverage/cli/commands/scan.py](../../src/dbt_coverage/cli/commands/scan.py):

- `--render-mode [auto|mock|compiled]`
- `--compiled-dir PATH`

CLI flags override `dbtcov.yml`, which overrides defaults.

### 4.8 Reporter notes

- Console header displays `render: COMPILED  files=498  parsed=492  ...` and emits a dim one-line note: *"Line numbers reference compiled SQL; source-line mapping is v2."* when any finding comes from a COMPILED node.
- SARIF: `runs[0].properties.render_mode = "COMPILED"`; each result with `compiled_path` gets `properties.compiled_path`.
- JSON: `Finding.compiled_path` surfaces via `ParsedNode.compiled_path` attached at the orchestrator level.

## 5. Algorithm

```
resolve_compiled_path(source_file):
    rel = source_file.relative_to(project_root)
    candidate = compiled_dir / rel
    return candidate if candidate.exists() else None

render(file):
    compiled_path = resolve_compiled_path(file.path)
    if compiled_path is None:
        return fallback_renderer.render(file)     # degrade to MOCK
    content = compiled_path.read_text()
    n_lines = content.count("\n") + 1
    return ParsedNode(
        file_path=file.path,                      # source path stays
        compiled_path=compiled_path,
        source_sql=file.content,
        rendered_sql=content,
        render_mode=COMPILED,
        render_uncertain=False,
        line_map={i: i for i in range(1, n_lines + 1)},
        source_line_map={i: i for i in range(1, n_lines + 1)},
    )
```

## 6. Risks

- Stale `target/compiled` — findings reflect the last `dbt compile` snapshot. Mitigation: show `manifest.json` generated_at in the console header when `> 24h` old.
- Compiled SQL differs from source: line numbers in findings don't match source files. Documented; v2 adds line remapping.
- Models with pre-hooks / post-hooks — the compiled file contains only the SELECT body, which is exactly what we want for analysis.

## 7. Acceptance

- `parsed` on pontus-models rises from 212 to ≥ 480.
- `render_uncertain` drops to 0 for compiled models.
- Existing 75 tests remain green.
- New tests: `test_compiled_renderer.py` (unit), `test_compiled_mode_e2e.py` (integration with a pre-seeded `target/compiled/`).
