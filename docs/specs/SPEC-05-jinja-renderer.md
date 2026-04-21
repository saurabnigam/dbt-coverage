# SPEC-05 — Jinja Renderer (MOCK mode)

**Status:** draft
**Depends on:** SPEC-01, SPEC-03
**Blocks:** SPEC-06, SPEC-18
**Critical path** — highest failure-mode risk in the pipeline.

---

## 1. Purpose

Convert dbt Jinja-templated SQL into **parsable SQL** (via sqlglot later) while:
- Preserving the **semantic identity** of dbt constructs (`ref`, `source`, `config`) as sentinel identifiers.
- Producing a **line map** (rendered line → source line) so Findings point at the right source line.
- Handling **macros, conditionals, loops** without executing them.
- **Failing gracefully** — mark uncertain, never crash.

Phase-1 scope: **MOCK mode only.** PARTIAL and DBT modes are phase-3.

---

## 2. Non-goals

- No warehouse access.
- No `dbt compile` invocation.
- No adapter dispatch resolution (`adapter.dispatch(...)` → `render_uncertain=True`).
- No full macro execution — stubbed or skipped.

---

## 3. Design principles (from plan §Design principles)

1. **Never execute dbt logic.** Only simulate structure.
2. **Preserve identity:** `{{ ref('orders') }}` → `__REF_orders__` (valid SQL identifier, retrievable for lineage).
3. **Fail gracefully:** any rendering failure sets `render_uncertain=True`; downstream AST rules are skipped for that node; text-based rules still run.

---

## 4. Module layout

```
src/dbt_coverage/parsers/
  __init__.py
  jinja_renderer.py        # public: render() entrypoint
  mock_context.py          # dbt-mock functions (ref/source/config/var/this/target)
  line_map.py              # marker-based line mapping
  macro_indexer.py         # scan project macros folder, build stub registry
```

---

## 5. API Surface

### 5.1 `jinja_renderer.py`

```python
from pathlib import Path
from dbt_coverage.core import ParsedNode, RenderMode
from dbt_coverage.scanners import ProjectIndex, IndexedFile

class JinjaRenderer:
    def __init__(
        self,
        project_index: ProjectIndex,
        mode: RenderMode = RenderMode.MOCK,
        cache_dir: Path | None = None,
    ):
        """
        Initializes the Jinja2 Environment once, indexes macros, prepares mock context.
        cache_dir enables per-file render cache keyed on source_hash (phase 2 incremental).
        """

    def render(self, file: IndexedFile) -> ParsedNode:
        """
        Returns a ParsedNode with:
          - rendered_sql, line_map, refs, sources, config, macros_used, render_mode, render_uncertain
          - ast=None, parse_success=True (SPEC-06 fills these)
        Never raises on rendering failure; sets render_uncertain=True instead.
        """

    def render_all(self, files: list[IndexedFile]) -> list[ParsedNode]:
        """Parallel rendering via ProcessPoolExecutor. Deterministic output order."""
```

### 5.2 `mock_context.py`

```python
class CapturedConfig:
    """Mutable sink passed into Jinja env; mock `config()` writes here."""
    def __init__(self) -> None:
        self.data: dict = {}
    def set(self, **kwargs) -> str: ...   # merges kwargs, returns ""

def build_mock_context(
    macro_registry: "MacroRegistry",
    captured_config: CapturedConfig,
    captured_refs: list[str],
    captured_sources: list[tuple[str, str]],
    captured_macros: list[str],
) -> dict:
    """
    Returns the globals dict for a Jinja2 Environment.
    Functions close over captured_* lists/dicts so the renderer can extract them post-render.
    """
```

**Mock functions (non-negotiable contract):**

| Jinja call | Returns | Captures |
|---|---|---|
| `ref('name')` or `ref('pkg','name')` | `"__REF_name__"` | Appends to `captured_refs` |
| `source('src','tbl')` | `"__SRC_src_tbl__"` | Appends to `captured_sources` |
| `config(**kw)` | `""` (empty) | Merges into `captured_config.data` |
| `var('name', default=None)` | `str(default)` if given else `"__VAR_name__"` | — |
| `this` | `"__THIS__"` (a `This` class with `__str__`) | — |
| `target.name` | `"dev"` | — |
| `target.schema` | `"public"` | — |
| `target.type` | adapter name from project_info, else `"postgres"` | — |
| `is_incremental()` | `False` (deterministic branch selection) | — |
| `execute` | `True` (execution phase, deterministic) | — |
| `adapter.dispatch(name, pkg)()` | **Raises `_AdapterDispatchUnsupported`** → caught by renderer → `render_uncertain=True` | — |
| `{{ my_macro(x) }}` (known macro) | `"__MACRO_my_macro__"` | Appends to `captured_macros` |
| `{{ unknown_macro(x) }}` | Jinja2's default UndefinedError → caught → `render_uncertain=True` | — |

**Why these exact strings:**
- `__REF_name__` is a valid SQL identifier in all target dialects (letters + underscores, no digit-start), so sqlglot parses it as a plain table identifier.
- Double-underscore prefix/suffix makes extraction via regex trivial and collision-proof with real dbt column names (which use single underscores).

### 5.3 `line_map.py`

```python
def inject_line_markers(source_sql: str) -> str:
    """Insert `-- DBTCOV_LINE:N` comments before each source line."""

def extract_line_map(rendered_with_markers: str) -> tuple[str, dict[int, int]]:
    """
    Walk rendered output, strip markers, build {rendered_line: source_line}.
    Returns (clean_rendered_sql, line_map).
    """
```

**Algorithm:**
- Before rendering: prepend each source line `N` with `-- DBTCOV_LINE:N\n`. Jinja treats SQL comments as text, so they survive rendering intact unless a macro strips them (rare).
- After rendering: walk each line of output; when we see `-- DBTCOV_LINE:N`, register the next line's rendered number → N mapping. Remove the marker.
- For lines without a marker (e.g. inside expanded macros), inherit the last seen source line.
- Edge: `{% if %}` false-branch lines disappear from output, so `line_map` correctly skips them.

**Fallback (if markers are stripped by custom Jinja filter):** offset-based approximation — rendered_line × (source_total/rendered_total), logged as `render_uncertain=True` for line accuracy.

### 5.4 `macro_indexer.py`

```python
from pathlib import Path

class MacroRegistry(BaseModel):
    known_macros: set[str]           # all top-level macro names found in macro_paths
    def is_known(self, name: str) -> bool: ...

def index_macros(project_index: ProjectIndex) -> MacroRegistry:
    """
    Scan all files in project_index.macros for `{% macro NAME(...) %}` definitions.
    Regex-based (avoids full Jinja parse). Returns registry of names only.
    """
```

**Why regex over Jinja AST parse:** a regex `r"{%\s*macro\s+(\w+)\s*\("` catches ≥99% of real-world macros. Using Jinja's own parser here creates a circular dep. Good-enough for MOCK mode's job of knowing "does this macro exist in this project".

---

## 6. Render pipeline (step-by-step)

For each `IndexedFile`:
1. `markers = inject_line_markers(file.content)`
2. Build a fresh Jinja2 `Template` from `markers` using the pre-configured `Environment`.
3. Reset per-file sinks: `captured_config = CapturedConfig()`, `captured_refs = []`, etc.
4. Attempt `template.render()` with the mock context.
5. On success → `extract_line_map(output)` → `(rendered_sql, line_map)`.
6. On any exception (UndefinedError, AdapterDispatchUnsupported, TemplateError):
   - `rendered_sql = file.content` (fall back to raw source so text-rules still work)
   - `line_map = {i: i for i in range(1, count_lines+1)}` (identity map)
   - `render_uncertain = True`
7. Construct `ParsedNode(file_path=file.path, source_sql=file.content, rendered_sql=..., line_map=..., config=captured_config.data, refs=captured_refs, sources=captured_sources, macros_used=captured_macros, render_mode=MOCK, render_uncertain=flag, ast=None, parse_success=True)`.
8. Return.

---

## 7. Edge cases

| Case | Expected behavior |
|---|---|
| `{{ ref('orders') }}` | `__REF_orders__` in output, `"orders"` in refs |
| `{{ ref('pkg', 'orders') }}` | `__REF_orders__` (ignore package for MOCK); captured as `"orders"` |
| `{{ source('raw', 'events') }}` | `__SRC_raw_events__` in output, `("raw", "events")` in sources |
| `{{ var('x') }}` no default | `__VAR_x__` |
| `{{ var('x', 100) }}` | `100` |
| `{{ config(materialized='incremental', partition_by='dt') }}` | Empty string in output; `{"materialized": "incremental", "partition_by": "dt"}` in `ParsedNode.config` |
| `{% if is_incremental() %}...{% endif %}` | False branch selected; `render_uncertain=False` |
| `{% if target.name == 'prod' %}...{% endif %}` | `target.name="dev"` so false branch; no uncertainty |
| `{% for col in ['a','b'] %}{{col}},{% endfor %}` | Expanded: `a,b,` |
| `{% for col in columns %}` (columns unknown) | `UndefinedError` → `render_uncertain=True` |
| `{{ adapter.dispatch('fn')() }}` | `render_uncertain=True` |
| `{{ my_macro(x) }}` known in registry | `__MACRO_my_macro__`; `"my_macro"` in `macros_used` |
| `{{ unknown_macro(x) }}` | `render_uncertain=True` |
| Comment `-- hello` in source | Passes through unchanged |
| Block comment `/* ... */` | Passes through |
| File with only Jinja (no SQL) | `rendered_sql` may be empty; `parse_success` will fail in SPEC-06 |
| File with `{% raw %}...{% endraw %}` | Content inside rendered literally |
| Extremely nested macros (depth > 50) | Jinja RecursionError → caught → `render_uncertain=True` |
| Non-UTF-8 content | Already filtered by SPEC-03; not reachable here |

---

## 8. Test plan (`tests/unit/parsers/`)

### 8.1 `test_mock_context.py`
- `ref('orders')` → `"__REF_orders__"`, captured
- `ref('pkg','x')` → `"__REF_x__"`, captured as `"x"`
- `source('a','b')` → `"__SRC_a_b__"`, captured as `("a","b")`
- `config(x=1,y=2)` → `""` + captured dict `{"x":1,"y":2}`
- `var('x')` no default → `"__VAR_x__"`
- `var('x', 42)` → `"42"`
- `is_incremental()` → `False`, `target.name` → `"dev"`
- `adapter.dispatch(...)()` → raises `_AdapterDispatchUnsupported`

### 8.2 `test_line_map.py`
- 10-line SQL with no Jinja → `line_map == {i: i for i in 1..10}` after round-trip
- Source has 5 lines; rendered has 7 (macro expanded) → each rendered line maps to correct source line
- Conditional false branch → mapped source lines absent from line_map

### 8.3 `test_jinja_renderer.py` (golden-file)
- `tests/fixtures/jinja_cases/*.sql` — each with a `.golden.sql` + `.golden.json` (refs/sources/config/uncertain)
- Cases to cover:
  - plain SQL no Jinja
  - simple `ref` + `source`
  - `config` with multiple kwargs
  - incremental model with `is_incremental()` conditional
  - known macro invocation
  - unknown macro → uncertain
  - adapter.dispatch → uncertain
  - for-loop over literal list
  - for-loop over unknown → uncertain
  - recursive macro depth exceeded → uncertain

### 8.4 `test_macro_indexer.py`
- 3 macros defined across 2 files → all 3 names in registry
- Macro with Jinja comment above → still detected
- Arg whitespace variations `{% macro foo (x) %}`, `{%macro bar(x)%}` → both detected

### 8.5 `test_render_parallel.py`
- `render_all([file1, file2, file3])` returns results in input order
- Parallel render produces identical output to sequential

**Coverage target:** 90%+. Branch coverage critical for the error paths.

---

## 9. Acceptance criteria

- [ ] `render()` on every file in `tests/fixtures/sample_dbt_project/models/` completes without raising
- [ ] ≥95% of fixture files render with `render_uncertain=False`
- [ ] All `refs`, `sources`, `config` extractable from rendered output match expected (golden tests)
- [ ] Line map correctness verified on fixture with ≥3 Jinja constructs (macro + if + for)
- [ ] `ruff`, `mypy --strict` clean
- [ ] `pytest tests/unit/parsers/` ≥90% coverage
- [ ] Parallel render of 100 files ≤ 2× sequential time (not stricter — overhead can dominate small files)
- [ ] `jinja2` is the only new runtime dep introduced by this spec

---

## 10. Open questions

- Should we emit warnings when a macro uses `adapter.dispatch` and we can't resolve it? **Proposal:** yes, aggregated count in `render_stats`, not per-file log spam.
- Does `{% raw %}` survive our line marker injection? Inject markers only outside `{% raw %}` blocks — add a pre-pass to skip raw regions. Tracked in test 8.3.
