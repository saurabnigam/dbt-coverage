# SPEC-03 — Source Scanner

**Status:** draft
**Depends on:** SPEC-01, SPEC-02
**Blocks:** SPEC-05, SPEC-09, SPEC-18

---

## 1. Purpose

Walk the dbt project directory and build a **virtual file index** — the equivalent of SonarQube's `SensorContext`. Every downstream module operates on this index instead of hitting the filesystem directly.

Outputs a `ProjectIndex` containing:
- `.sql` files grouped by kind (model, singular test, macro, seed)
- `.yml`/`.yaml` schema files and their parsed content (models/sources/exposures/unit_tests)
- `.md` doc blocks
- Source hashes (for incremental caching, SPEC-19)

---

## 2. Non-goals

- No Jinja rendering (SPEC-05)
- No SQL parsing (SPEC-06)
- No dbt artifact reading (SPEC-04; phase 2)
- No git operations (SPEC-19)

---

## 3. Module layout

```
src/dbt_coverage/scanners/
  __init__.py
  source_scanner.py         # scan() entrypoint
  project_index.py          # ProjectIndex, IndexedFile, ModelEntry, YamlEntry
  yaml_parser.py            # load schema.yml files, normalize into typed entries
```

---

## 4. API Surface

### 4.1 `project_index.py`

```python
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

SqlKind = Literal["model", "singular_test", "macro", "seed", "snapshot", "analysis"]

class IndexedFile(BaseModel):
    model_config = {"extra": "forbid"}
    path: Path                       # relative to project_root
    absolute_path: Path
    content: str                     # raw file contents
    source_hash: str                 # sha256[:16] of content, for caching

class ModelEntry(BaseModel):
    model_config = {"extra": "forbid"}
    node_id: str                     # "model.<project>.<name>"
    name: str
    sql_file: IndexedFile
    yml_meta: "YamlModelMeta | None" = None

class YamlColumnMeta(BaseModel):
    model_config = {"extra": "forbid", "extra": "allow"}   # dbt allows arbitrary meta
    name: str
    description: str | None = None
    tests: list = Field(default_factory=list)              # raw test entries
    meta: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

class YamlModelMeta(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    description: str | None = None
    columns: list[YamlColumnMeta] = Field(default_factory=list)
    tests: list = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    unit_tests: list = Field(default_factory=list)         # dbt 1.8+
    file_path: Path                                        # yml source, for error reporting
    line: int                                              # start line of this model block

class YamlSourceMeta(BaseModel):
    model_config = {"extra": "allow"}
    source_name: str
    table_name: str
    description: str | None = None
    columns: list[YamlColumnMeta] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    file_path: Path
    line: int

class ProjectIndex(BaseModel):
    model_config = {"extra": "forbid", "arbitrary_types_allowed": True}
    project_root: Path                                # absolute
    project_name: str
    models: dict[str, ModelEntry] = Field(default_factory=dict)   # keyed by node_id
    singular_tests: list[IndexedFile] = Field(default_factory=list)
    macros: list[IndexedFile] = Field(default_factory=list)
    seeds: list[IndexedFile] = Field(default_factory=list)
    sources: dict[tuple[str, str], YamlSourceMeta] = Field(default_factory=dict)  # (source, table)
    exposures: list = Field(default_factory=list)     # raw exposure blocks
    yml_files: list[IndexedFile] = Field(default_factory=list)
    doc_blocks: dict[str, str] = Field(default_factory=dict)      # {{ doc('name') }} -> content
    scan_errors: list[str] = Field(default_factory=list)          # non-fatal issues, e.g. malformed YAML
```

### 4.2 `source_scanner.py`

```python
def scan(
    project_info: "DbtProjectInfo",
    config: "DbtcovConfig",
) -> ProjectIndex:
    """
    Walk project_root per project_info.model_paths/test_paths/macro_paths/seed_paths.
    Apply config.exclude glob patterns.
    Load and parse all schema .yml files.
    Returns a fully populated ProjectIndex.
    """
```

**Walk algorithm:**
1. For each `model_paths` entry, recurse; collect `.sql` files → `ModelEntry`.
2. For each `test_paths`, recurse; collect `.sql` → `singular_tests` (skip `.yml`, those are picked up separately).
3. For each `macro_paths`, recurse; collect `.sql` → `macros`.
4. For each `seed_paths`, collect `.csv` paths (content hashed but not loaded — seeds are data, not code).
5. Walk **all** `model_paths`, `test_paths`, and root for `.yml`/`.yaml` files → parse into `YamlModelMeta`, `YamlSourceMeta`, exposures, unit_tests, doc blocks.
6. Walk for `.md` files; extract `{% docs name %}...{% enddocs %}` blocks → `doc_blocks`.
7. Cross-link: after YAML pass, attach `YamlModelMeta` to `ModelEntry.yml_meta` by name match.

**Source hash:** `hashlib.sha256(content.encode()).hexdigest()[:16]` — keyed for SPEC-19 cache.

**Node ID convention:** `model.<project_name>.<model_file_basename_without_ext>`. Matches dbt's own convention so manifest.json integration later is seamless.

### 4.3 `yaml_parser.py`

```python
def parse_schema_yml(
    path: Path,
    content: str,
) -> tuple[list[YamlModelMeta], list[YamlSourceMeta], list, list[str]]:
    """
    Parse a single schema.yml file.
    Returns: (models, sources, exposures, warnings).

    Uses ruamel.yaml (round-trip mode) to preserve line numbers on each block.
    Warnings are non-fatal (e.g. "key 'tset' looks like a typo of 'tests'").
    """
```

**Why ruamel.yaml:** preserves source line numbers per block — essential for Finding.line accuracy when a rule flags a schema-level issue (e.g. "model has no description"). Standard `pyyaml` drops line info.

**Dependency decision:** adds `ruamel.yaml` to deps. Small (~150KB) and well-maintained. Alternative is pyyaml + manual line tracking, not worth the complexity.

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| Binary file in a scan path | Skip; log to `scan_errors` |
| File > 10 MB | Skip; log (protects memory, dbt SQL rarely exceeds this) |
| Unreadable file (permission denied) | Skip; log |
| Malformed YAML | Skip that file, add warning to `scan_errors`, don't crash |
| Duplicate model name across paths | First wins; warning in `scan_errors` |
| `.sql` file with no matching YAML entry | `ModelEntry.yml_meta = None` (rule Q003 fires later) |
| YAML entry with no matching `.sql` file | Added to `scan_errors`; not added as a model |
| `{% docs %}` block with no `{% enddocs %}` | Skip; warning |
| Empty `model_paths` | Empty `models` dict; no error |
| Circular symlink in walk | Detected by `os.walk(followlinks=False)` default |
| Non-UTF-8 file | Try UTF-8 → latin-1 → skip with warning |

---

## 6. Test plan (`tests/unit/scanners/`)

### 6.1 `test_source_scanner.py`
- Minimal project (1 model, 1 schema.yml) → index contains 1 model with `yml_meta` populated.
- Project with `.sql` but no YAML → `yml_meta=None` on that model.
- `exclude: ["models/archive/**"]` in config → files under `models/archive/` absent from index.
- Project with binary file in `models/` → skipped, logged.
- Source hash stable across calls on identical content.
- Symlinked model dir → not followed (default `followlinks=False`).

### 6.2 `test_yaml_parser.py`
- Schema with 2 models and 1 source → all 3 parsed with correct `line` values.
- Schema with `version: 2` preamble → stripped correctly.
- Schema with unit_tests block (dbt 1.8+) → populated on `YamlModelMeta.unit_tests`.
- Malformed YAML (missing closing bracket) → returns empty lists, warning in output.
- Doc block `{% docs foo %}hello{% enddocs %}` in `.md` → `doc_blocks["foo"] = "hello"`.

### 6.3 `test_project_index.py`
- Round-trip `ProjectIndex` through Pydantic dump (excluding `IndexedFile.content` for size) → structural fields preserved.
- Node ID generation matches `model.<project>.<name>` format.

**Coverage target:** 90%+ (some error-branch code paths exercised by integration tests only).

---

## 7. Acceptance criteria

- [ ] `scan(project_info, config)` on `examples/basic_project/` returns `ProjectIndex` with `len(models) > 0` and no `scan_errors`
- [ ] All YAML line numbers in `YamlModelMeta.line` point at the actual model block (verified by visual check on 1 fixture)
- [ ] `ruff`, `mypy --strict` clean
- [ ] `pytest tests/unit/scanners/` ≥90% coverage
- [ ] Zero imports from `dbt`, `sqlglot`, `jinja2`, `sqlfluff`
- [ ] Scan of a 500-model synthetic project completes in <2 seconds (benchmark, not strict pass criterion)

---

## 8. Open questions

- Do we need to detect dbt v1 vs v2 schema format differences? **Proposal:** no — dbt has been on v2 since 2020; reject v1 with clear error.
- Cache `ProjectIndex` on disk? **Proposal:** no, SPEC-19 handles per-model caching; whole-index cache adds invalidation complexity for marginal win.
