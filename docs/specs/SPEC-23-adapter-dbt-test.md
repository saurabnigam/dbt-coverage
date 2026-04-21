# SPEC-23 — Adapter: dbt-test (manifest + run_results)

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-21 (Adapter framework), SPEC-22 (consumer of `TestResult`)
**Blocks:** none

---

## 1. Purpose

First concrete built-in `Adapter` under SPEC-21. Ingests the two standard dbt artefacts:

- `target/manifest.json` — declared tests, unit tests, test-to-model ref edges.
- `target/run_results.json` — pass/fail/error status of the most recent `dbt test` (or `dbt build`) run.

Emits a list of `TestResult` objects in the shared domain model. Handles dbt schema-version drift from 1.5 through 1.8+. Fails soft when artefacts are missing, corrupt, or partially populated.

This adapter is the backbone of "JaCoCo-style" evidence-based coverage: without `run_results.json`, we only know tests were *declared*; with it, we know which ones *passed*, and SPEC-22 `test_meaningful` becomes evidence-based.

---

## 2. Non-goals

- Does not run `dbt test` via `run` mode in v1. dbt invocations have too many environment prerequisites (profiles, target, credentials) to be safely invoked by a static analysis tool. `run` mode raises `AdapterNotRunnableError("dbt-test adapter is read-only in v1")`. Future spec can add a `dbt test --no-compile` shell-out under user opt-in.
- Does not emit `Finding`s. Failed tests are surfaced via `TestResult.status=FAIL` only; SPEC-22 decides what that means for coverage. Converting failing tests to findings is a separate adapter (`SPEC-25-adapter-dbt-test-findings`, future).
- Does not compute coverage. That's SPEC-22's job.
- Does not support pre-dbt-1.5 artefacts. dbt 1.4 has a different manifest schema; explicit `UNSUPPORTED_SCHEMA` failure with a helpful message.

---

## 3. Module layout

```
src/dbt_coverage/adapters/dbt_test/
  __init__.py        # re-exports DbtTestAdapter
  adapter.py         # DbtTestAdapter class (implements Adapter)
  manifest.py        # parse_manifest() -> ManifestIndex
  run_results.py     # parse_run_results() -> RunResultsIndex
  schema_versions.py # supported version matrix
```

---

## 4. Supported dbt schema versions

| dbt version range | manifest schema_version | run_results schema_version | Status |
|---|---|---|---|
| 1.5.x | `v10` | `v4` | Supported |
| 1.6.x | `v11` | `v5` | Supported |
| 1.7.x | `v12` | `v6` | Supported |
| 1.8.x | `v12` (same as 1.7) | `v6` | Supported — adds `unit_tests` |
| 1.9.x+ | `v13` (forward-compat best-effort) | `v7`+ | Supported (read only the fields we know; log at DEBUG) |
| ≤ 1.4 | older | older | UNSUPPORTED_SCHEMA — adapter emits one `ADAPTER_FAILED` finding, returns empty results |

Version detection reads `metadata.dbt_schema_version` (e.g. `"https://schemas.getdbt.com/dbt/manifest/v12.json"`). We match on the trailing `/v(\d+)\.json` integer.

---

## 5. Data shape read from artefacts

### 5.1 From `manifest.json`

Loaded into an internal `ManifestIndex` (not exported — only `TestResult` is public):

```python
@dataclass(frozen=True)
class ManifestTest:
    unique_id: str                  # e.g. "test.proj.not_null_dim_customers_id.123"
    name: str                       # e.g. "not_null_dim_customers_id"
    test_metadata_name: str | None  # e.g. "not_null" (generic), None for singular
    namespace: str | None           # e.g. "dbt_utils" for namespaced generics
    data_test_type: str             # "generic" | "singular" | "unit" (1.8+)
    refs: list[str]                 # referenced model unique_ids
    column_name: str | None
    file_path: Path | None          # absolute path to tests/*.sql or schema.yml

@dataclass(frozen=True)
class ManifestIndex:
    dbt_version: str | None
    schema_version: int
    tests: list[ManifestTest]
```

#### 5.1.1 Field extraction per schema version

Node iteration: `nodes.values()` for generic/singular tests where `resource_type == "test"`. For unit tests (1.8+), iterate `unit_tests.values()` — note the separate top-level key. `unit_tests` missing on 1.5–1.7 → silently skipped.

Mapping:

| Field | Source |
|---|---|
| `unique_id` | `node.unique_id` |
| `name` | `node.name` |
| `test_metadata_name` | `node.test_metadata.name` (generic tests) else None |
| `namespace` | `node.test_metadata.namespace` if present |
| `data_test_type` | `"unit"` (from unit_tests dict), else `node.test_metadata.kind or "generic" if test_metadata else "singular"` |
| `refs` | flattened `node.refs` (v11 `{"name": ...}`, v12 `["proj","model"]`); dereferenced to `unique_id`s via the manifest's `parent_map[test_unique_id]` (falling back to own `depends_on.nodes`) |
| `column_name` | `node.column_name` (None for singular / unit tests) |
| `file_path` | `Path(node.original_file_path)` if present, else `Path(node.path)` |

#### 5.1.2 Computed `test_kind` normalisation

`TestResult.test_kind` is built as:

```python
if data_test_type == "singular":
    test_kind = "singular"
elif data_test_type == "unit":
    test_kind = "unit_test"
elif namespace:
    test_kind = f"{namespace}.{test_metadata_name}"     # e.g. "dbt_expectations.expect_column_values_to_not_be_null"
else:
    test_kind = test_metadata_name or "unknown"         # "not_null", "unique", "relationships", ...
```

This produces the keys that the SPEC-22 classifier matches against.

### 5.2 From `run_results.json`

```python
@dataclass(frozen=True)
class RunResultEntry:
    unique_id: str           # matches ManifestTest.unique_id
    status: TestStatus       # normalised (see below)
    message: str | None
    execution_time: float

@dataclass(frozen=True)
class RunResultsIndex:
    dbt_version: str | None
    schema_version: int
    results_by_unique_id: dict[str, RunResultEntry]
```

Status mapping (dbt result `status` string → our `TestStatus`):

| dbt `status` | `TestStatus` |
|---|---|
| `"pass"` | `PASS` |
| `"fail"` | `FAIL` |
| `"error"` | `ERROR` |
| `"warn"` | `PASS` (treat as passing with diagnostic message) |
| `"skipped"` | `SKIPPED` |
| anything else | `UNKNOWN` |

Only entries with `resource_type in {"test", "unit_test"}` are indexed. Build-time non-test results (`"model"`, `"seed"`) are ignored.

---

## 6. Adapter class

```python
class DbtTestAdapter:
    name: str = "dbt-test"
    display_name: str = "dbt test (manifest + run_results)"
    output_kinds: tuple[str, ...] = ("test_results",)
    default_report_path = None                      # this adapter needs two files; handled in discover
    default_mode = AdapterMode.AUTO

    def discover(self, project_root: Path, cfg: AdapterConfig) -> Path | None:
        # For this adapter "report" is treated as the run_results.json.
        # Manifest path is a second config key (see 6.1).
        run_results = self._resolve(project_root, cfg, key="report",
                                    default=Path("target/run_results.json"))
        if run_results.exists():
            return run_results
        # Fall back: manifest alone is enough for declared-only mode.
        manifest = self._resolve(project_root, cfg, key="manifest",
                                 default=Path("target/manifest.json"))
        return manifest if manifest.exists() else None

    def is_runnable(self) -> bool:
        return False   # v1 is read-only

    def run(self, project_root: Path, cfg: AdapterConfig) -> Path:
        raise AdapterNotRunnableError("dbt-test adapter is read-only in v1")

    def read(self, report_path: Path, cfg: AdapterConfig) -> AdapterResult:
        project_root = report_path.parent.parent if report_path.name in ("run_results.json", "manifest.json") else Path.cwd()
        manifest_path = self._resolve(project_root, cfg, key="manifest",
                                      default=Path("target/manifest.json"))
        manifest = parse_manifest(manifest_path) if manifest_path.exists() else None

        if manifest is None:
            # Nothing usable without a manifest.
            return AdapterResult(
                adapter=self.name,
                invocation=AdapterInvocation(
                    adapter=self.name, mode=AdapterMode.READ,
                    status="read_failed",
                    message=f"manifest.json not found at {manifest_path}",
                ),
            )

        run_results = None
        if report_path.name == "run_results.json":
            run_results = parse_run_results(report_path)

        test_results = self._build_test_results(manifest, run_results)

        return AdapterResult(
            adapter=self.name,
            test_results=test_results,
            invocation=AdapterInvocation(
                adapter=self.name,
                mode=cfg.mode,
                tool_version=manifest.dbt_version,
                report_path=report_path,
                status="ok",
            ),
        )

    def tool_version(self) -> str | None:
        # We can only know the version after read(); placeholder for invocations not yet executed.
        return None

    @staticmethod
    def _resolve(project_root, cfg, key, default) -> Path:
        override = (cfg.params or {}).get(key) or getattr(cfg, key, None)
        p = Path(override) if override else default
        return p if p.is_absolute() else project_root / p

    def _build_test_results(self, manifest: ManifestIndex,
                            run_results: RunResultsIndex | None) -> list[TestResult]:
        # Dedup manifest tests by unique_id (should already be unique, defensive).
        out: dict[str, TestResult] = {}
        for t in manifest.tests:
            status = TestStatus.UNKNOWN
            if run_results is not None:
                entry = run_results.results_by_unique_id.get(t.unique_id)
                if entry is not None:
                    status = entry.status
            model_unique_id = t.refs[0] if t.refs else None
            out[t.unique_id] = TestResult(
                test_name=t.name,
                test_kind=_normalise_kind(t),
                model_unique_id=model_unique_id,
                column_name=t.column_name,
                status=status,
                file_path=_relpath(t.file_path) if t.file_path else None,
                origin=self.name,
                raw_kind=t.test_metadata_name,
            )
        return list(out.values())
```

### 6.1 Config surface for this adapter

```yaml
adapters:
  dbt-test:
    enabled: true
    mode: auto                            # run blocked in v1
    report: target/run_results.json       # standard AdapterConfig field
    params:
      manifest: target/manifest.json      # adapter-specific
      treat_warn_as_pass: true            # default true
      ignore_missing_run_results: false   # default false -> status=UNKNOWN when absent
```

`treat_warn_as_pass=false` flips the §5.2 `warn` mapping to `TestStatus.FAIL`. This lets strict teams count warns as gate-relevant.

---

## 7. Parsing details

### 7.1 `parse_manifest(path)`

```python
def parse_manifest(path: Path) -> ManifestIndex:
    raw = _load_json(path)                 # json.loads with size guard (see 7.3)
    schema = _extract_schema_version(raw)
    if schema < 10:
        raise UnsupportedSchemaError(schema)

    tests: list[ManifestTest] = []
    for node in (raw.get("nodes") or {}).values():
        if node.get("resource_type") != "test":
            continue
        tests.append(_node_to_test(node, schema))
    if schema >= 12:
        for node in (raw.get("unit_tests") or {}).values():
            tests.append(_unit_to_test(node, schema))

    return ManifestIndex(
        dbt_version=raw.get("metadata", {}).get("dbt_version"),
        schema_version=schema,
        tests=tests,
    )
```

### 7.2 `parse_run_results(path)`

```python
def parse_run_results(path: Path) -> RunResultsIndex:
    raw = _load_json(path)
    schema = _extract_schema_version(raw)
    if schema < 4:
        raise UnsupportedSchemaError(schema)
    out: dict[str, RunResultEntry] = {}
    for r in raw.get("results") or []:
        uid = r.get("unique_id")
        if not uid:
            continue
        # Only keep test-like nodes.
        if not (uid.startswith("test.") or uid.startswith("unit_test.")):
            continue
        out[uid] = RunResultEntry(
            unique_id=uid,
            status=_map_status(r.get("status"), treat_warn_as_pass=True),
            message=r.get("message"),
            execution_time=float(r.get("execution_time") or 0.0),
        )
    return RunResultsIndex(
        dbt_version=raw.get("metadata", {}).get("dbt_version"),
        schema_version=schema,
        results_by_unique_id=out,
    )
```

### 7.3 `_load_json`

- `json.loads(path.read_bytes())` — fast path.
- Size guard: if `path.stat().st_size > 256 MB`, read via streaming (`ijson.items`) and accumulate only the keys we need. This prevents OOM on mega-projects.
- Corrupt JSON → `json.JSONDecodeError` → propagated; scheduler converts to `ADAPTER_FAILED` finding.

---

## 8. Failure modes

| Case | Behavior |
|---|---|
| `target/manifest.json` missing | `discover()` returns None → scheduler records `status=not_runnable`. |
| `target/run_results.json` missing, manifest present | Emit TestResults with `status=UNKNOWN`. Downstream SPEC-22 falls back to declared-only. |
| Manifest present, run_results present, but `dbt test` was a subset | Tests without entries in `run_results` → `status=UNKNOWN`. Tests with entries → real status. |
| Corrupt `manifest.json` | `read()` raises → scheduler isolates → `ADAPTER_FAILED` finding. Coverage dimensions fall back to declared-only. |
| Corrupt `run_results.json`, manifest fine | Log warning, emit declared-only results (all status=UNKNOWN). |
| Unsupported schema version (< v10 manifest) | Emit one `ADAPTER_FAILED` finding with `message="dbt manifest schema v{N} unsupported; upgrade dbt to >=1.5"`. Return empty `test_results`. |
| Future schema version (> v13) | Proceed; log DEBUG `"unknown schema vN; reading best-effort"`. Fields we don't recognise are ignored. |
| Ephemeral / source tests without a model ref | `model_unique_id=None`; SPEC-22 skips them. |
| Test with multiple `refs` (many-to-many) | We attribute to `refs[0]` only. Documented; realistic projects very rarely have this. |
| `unit_tests:` block referencing a non-existent model | Emitted anyway; SPEC-22 filters by `model_unique_id in parsed_nodes`. |
| Test `file_path` absolute outside project_root | `_relpath` returns None; `TestResult.file_path` left unset. Scheduler still accepts. |
| Status string that's neither pass/fail/error/warn/skipped | Mapped to `UNKNOWN`. |
| `treat_warn_as_pass=false` | `warn` → `FAIL`. Users opt in explicitly. |

---

## 9. Tests (`tests/unit/adapters/dbt_test/`)

### 9.1 `test_manifest.py` — fixture-driven
- v10 manifest (dbt 1.5) with two generic tests (`not_null`, `unique`) → 2 ManifestTest entries, kinds `"not_null"`, `"unique"`.
- v11 manifest with singular test → `data_test_type="singular"`, test_kind `"singular"`.
- v12 manifest with `unit_tests` entry → one additional ManifestTest with `data_test_type="unit"`, test_kind `"unit_test"`.
- v12 manifest with namespaced generic (`dbt_expectations.expect_column_values_to_not_be_null`) → test_kind `"dbt_expectations.expect_column_values_to_not_be_null"`.
- Missing `nodes` key → empty tests list, no exception.
- Corrupt JSON → `json.JSONDecodeError` propagates.
- Schema v9 (dbt 1.4) → `UnsupportedSchemaError`.

### 9.2 `test_run_results.py`
- All five status values map correctly.
- `warn` with `treat_warn_as_pass=true` → `PASS`; with false → `FAIL`.
- Entries with `resource_type != test` filtered out.
- Missing `results` key → empty index.
- Empty string `status` → `UNKNOWN`.

### 9.3 `test_adapter.py`
- `discover()` with no `target/` → None.
- `discover()` with only manifest → manifest path.
- `discover()` with both → run_results path.
- `read()` with manifest + run_results → TestResults populated with real statuses.
- `read()` with manifest only → all statuses `UNKNOWN`.
- `read()` with corrupt manifest → `json.JSONDecodeError` propagates (scheduler isolates).
- `is_runnable()` → False; `run()` → `AdapterNotRunnableError`.
- Output `origin="dbt-test"` on every TestResult.

### 9.4 Fixtures
- `tests/fixtures/dbt_artifacts/v10_minimal/{manifest.json, run_results.json}` — smallest legal artefacts.
- `tests/fixtures/dbt_artifacts/v12_unit_tests/{manifest.json, run_results.json}` — includes `unit_tests:` block.
- `tests/fixtures/dbt_artifacts/corrupt/{manifest.json}` — truncated JSON.

---

## 10. Acceptance criteria

- [ ] `DbtTestAdapter` registers under name `"dbt-test"` via the built-in list (SPEC-21 §6.1).
- [ ] All tests in §9 pass; ≥95% coverage on `adapter.py`, `manifest.py`, `run_results.py`.
- [ ] Running against the sample project's `target/` (SPEC-13 fixture extended) yields TestResults with `origin="dbt-test"` and correct statuses.
- [ ] No import of `dbt` anywhere (zero runtime dependency).
- [ ] `ruff` + `mypy --strict` clean.
- [ ] Memory footprint on a 10k-test manifest < 200 MB (via ijson fallback when configured).

---

## 11. Open questions

- Should we also emit a `coverage` metric `dbt_run_coverage = passed / declared` from this adapter? *Proposal: no — that's a classic "run rate" metric that belongs in SPEC-22 / reporter, not the adapter. Keep adapters minimal.*
- Should namespaced generics use the top-level namespace only (`dbt_expectations`) rather than the full name? *Proposal: keep full name. SPEC-22's classifier uses glob patterns (`dbt_expectations.*`) so both strategies work; full name is more debuggable.*
- Should we treat `skipped` tests as coverage? *Proposal: no — `SKIPPED` maps to not-passing in §6 of SPEC-22. A skipped test provides zero evidence.*
