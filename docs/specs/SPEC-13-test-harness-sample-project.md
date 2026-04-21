# SPEC-13 — Test Harness + Sample Project

**Status:** draft
**Depends on:** SPEC-01 (and every phase-1 spec for integration fixtures)
**Blocks:** —

---

## 1. Purpose

Define the test infrastructure every other spec writes against:

1. **Unit test layout** — mirrors the source package; one test module per source module.
2. **Shared fixtures** — `conftest.py` files that give specs a common idiom for building `ParsedNode`, `ProjectIndex`, `ScanResult`, etc. without each spec inventing its own.
3. **Golden-file harness** — compare scan outputs against checked-in goldens; regenerate on request.
4. **Sample dbt project** — small, realistic fixture exercising every phase-1 rule and both coverage dimensions.

This spec is **infrastructure for other specs** — it has no runtime code shipping to users.

---

## 2. Non-goals

- No performance benchmarks (tracked separately in perf-specific tests per spec).
- No property-based testing setup (hypothesis) — phase 2 if we see value.
- No flaky-test retry framework — failing tests should fail, not retry.
- No mutation testing (phase 3 maybe).
- No CI config — that's a platform/ops concern, tracked outside the spec list.

---

## 3. Directory layout

```
tests/
  __init__.py
  conftest.py                           # global fixtures
  unit/
    __init__.py
    conftest.py                         # unit-scope fixtures
    core/
    scanners/
    parsers/                            # SPEC-05 + SPEC-06
    graph/                              # SPEC-18
    analyzers/                          # SPEC-07 + rule packs
      packs/
        quality/                        # SPEC-08a
        performance/                    # SPEC-17a
    coverage/                           # SPEC-09a
    reporters/                          # SPEC-10a
    quality_gates/                      # SPEC-11
    cli/                                # SPEC-12
  integration/
    __init__.py
    conftest.py
    test_end_to_end.py                  # scan + gate against sample project
    test_cli.py                         # click CliRunner integration
    goldens/
      basic_project_findings.sarif
      basic_project_findings.json
      basic_project_coverage.json
      basic_project_console.txt
  fixtures/
    sample_dbt_project/                 # the sample project (§6)
    broken_project/                     # negative-case fixture (§7)
    minimal_project/                    # smallest valid dbt project (§8)
    test_plugin/                        # fixture rule-pack for entry-point discovery tests
```

---

## 4. Global test config

### 4.1 `pyproject.toml` / `pytest.ini`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-ra --strict-markers --cov=dbt_coverage --cov-report=term-missing --cov-fail-under=90"
markers = [
    "slow: tests >500ms (skipped by default unless --runslow)",
    "requires_dbt_core: tests needing the `[dbt-core]` extra",
    "integration: end-to-end tests touching the sample project",
]
filterwarnings = [
    "error",                     # fail on any warning
    "ignore::DeprecationWarning:sqlglot.*",  # third-party deprecations we can't control
]
```

`--runslow` flag defined in `tests/conftest.py`:
```python
def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False)

def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"): return
    skip_slow = pytest.mark.skip(reason="use --runslow")
    for item in items:
        if "slow" in item.keywords: item.add_marker(skip_slow)
```

### 4.2 Coverage gate

`--cov-fail-under=90` on package-wide coverage; per-spec acceptance criteria may require higher (e.g. rule-engine 92%, gate 95%).

---

## 5. Shared fixtures (`tests/unit/conftest.py`)

Each fixture is minimal — focused on what most tests need. Specs that need exotic shapes build ad-hoc.

```python
import pytest
from pathlib import Path
from dbt_coverage.core import (
    Finding, Severity, Category, FindingType, Tier, RenderMode,
    ParsedNode, CoverageMetric, ScanResult,
)

@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Creates a minimal valid dbt project skeleton in tmp_path; returns the root."""
    (tmp_path / "dbt_project.yml").write_text(
        'name: test_proj\nprofile: test_proj\nversion: "1.0"\n'
        'model-paths: ["models"]\ntarget-path: target\n'
    )
    (tmp_path / "models").mkdir()
    return tmp_path

@pytest.fixture
def make_parsed_node():
    """Factory for ParsedNode with sensible defaults."""
    def _make(
        file_path="models/foo.sql", source_sql="SELECT 1 AS x",
        rendered_sql="SELECT 1 AS x", ast=None,
        line_map=None, refs=(), sources=(),
        render_mode=RenderMode.MOCK, render_uncertain=False,
        parse_success=True, parse_error=None, **extra,
    ) -> ParsedNode:
        import sqlglot
        if ast is None and parse_success:
            ast = sqlglot.parse_one(rendered_sql)
        return ParsedNode(
            file_path=Path(file_path),
            source_sql=source_sql, rendered_sql=rendered_sql,
            ast=ast,
            line_map=line_map or {i: i for i in range(1, rendered_sql.count("\n") + 2)},
            config={}, refs=list(refs), sources=list(sources), macros_used=[],
            render_mode=render_mode, render_uncertain=render_uncertain,
            parse_success=parse_success, parse_error=parse_error, **extra,
        )
    return _make

@pytest.fixture
def make_finding():
    """Factory for Finding with sensible defaults."""
    def _make(
        rule_id="X001", severity=Severity.MAJOR, category=Category.QUALITY,
        type=FindingType.CODE_SMELL, tier=Tier.TIER_2_WARN,
        confidence=0.9, message="test finding",
        file_path="models/foo.sql", line=1, column=1,
        code_context="ctx:test", **extra,
    ) -> Finding:
        from dbt_coverage.core import compute_fingerprint
        return Finding(
            rule_id=rule_id, severity=severity, category=category, type=type,
            tier=tier, confidence=confidence, message=message,
            file_path=Path(file_path), line=line, column=column,
            fingerprint=compute_fingerprint(rule_id, str(file_path), code_context),
            **extra,
        )
    return _make

@pytest.fixture
def make_scan_result(make_finding):
    def _make(findings=(), coverage=(), project_root=Path("/tmp/proj"), **extra) -> ScanResult:
        return ScanResult(
            findings=list(findings),
            coverage=list(coverage),
            project_root=project_root,
            dbt_version_detected=None,
            render_stats=_empty_render_stats(),
            **extra,
        )
    return _make

@pytest.fixture
def sample_project_path() -> Path:
    return Path(__file__).parents[1] / "fixtures" / "sample_dbt_project"
```

**Convention:** prefer factories (`make_*`) over parametrized fixtures when tests need slight variations. Avoids combinatorial fixture explosion.

---

## 6. Sample dbt project (`tests/fixtures/sample_dbt_project/`)

Small, realistic project exercising every phase-1 rule and both coverage dimensions. Target: ~10 models.

### 6.1 Project layout

```
sample_dbt_project/
  dbt_project.yml
  README.md                             # explains the expected findings for humans
  models/
    staging/
      _staging__sources.yml
      _staging__models.yml
      stg_events.sql                    # clean, tested, documented
      stg_users.sql                     # triggers P001 (cross join, implicit)
    intermediate/
      _int__models.yml
      int_orders.sql                    # clean baseline
      int_orders_v2.sql                 # triggers R001 (91% similar to int_orders)
      int_user_agg.sql                  # triggers Q001 (SELECT *), no doc → doc miss
    marts/
      _marts__models.yml
      fct_orders.sql                    # triggers Q002 (no PK test declared)
      dim_users.sql                     # clean
      fct_revenue_daily.sql             # clean but only partially documented
    _sources.yml                        # raw sources block
  seeds/                                # empty (phase 1 doesn't use seeds)
  macros/
    _macros.yml
    cents_to_dollars.sql                # trivial macro, referenced by fct_revenue_daily
```

### 6.2 Expected findings

Checked in as `tests/integration/goldens/basic_project_findings.json` and `.sarif`.

| Rule | File | Line | Severity | Notes |
|---|---|---|---|---|
| Q002 | `models/marts/fct_orders.sql` | 1 | CRITICAL | Missing unique+not_null on `id` |
| P001 | `models/staging/stg_users.sql` | 12 | CRITICAL | Implicit cross join; no connecting WHERE |
| Q001 | `models/intermediate/int_user_agg.sql` | 3 | MAJOR | `SELECT *` in non-source model |
| R001 | `models/intermediate/int_orders.sql` | 1 | MAJOR | Paired with int_orders_v2 |
| R001 | `models/intermediate/int_orders_v2.sql` | 1 | MAJOR | Paired with int_orders |

Expected coverage on this project:
- `test`: some models tested (stg_events, stg_users, int_orders, dim_users, fct_revenue_daily) out of 8 → known ratio documented in golden.
- `doc`: mix of described and undescribed → known ratio in golden.

Expected gate result (default config): **FAIL** — Q002 and P001 are T1-CRITICAL, R001 is T1-MAJOR.

### 6.3 `dbtcov.yml` at project root

Same as the `dbtcov init` template (§5 of SPEC-12), so the integration test exercises the out-of-the-box experience.

### 6.4 Fixture-freezing discipline

- Sample-project files are **canonical**: any diff is a spec change, not a drive-by edit.
- Changing a fixture file requires regenerating goldens (§9).
- YAML files use `ruamel.yaml`-round-trippable style (stable ordering, no aliases) so goldens are diff-friendly.

---

## 7. Broken project (`tests/fixtures/broken_project/`)

Minimal layout that triggers every parse/render/scan error path:

- One model with unclosed Jinja (`{% if x %}…`) → render_uncertain.
- One model with invalid SQL (`SELECT FROM`) → parse failure.
- One model referencing a non-existent `{{ ref('nowhere') }}` → orphan in DAG.
- `schema.yml` with YAML syntax error → scanner warns, continues.

Used by:
- SPEC-05 tests (render fallback).
- SPEC-06 tests (parse recovery ladder).
- SPEC-18 DAG tests (orphan handling).
- SPEC-12 integration (scan doesn't crash).

---

## 8. Minimal project (`tests/fixtures/minimal_project/`)

One `dbt_project.yml` + one clean, tested, documented model. Used for:
- SPEC-02 discovery tests.
- SPEC-11 "clean project → gate PASS" test.
- Smoke tests where fixture richness is noise.

---

## 9. Golden-file harness

### 9.1 How goldens work

```python
# tests/integration/test_end_to_end.py
from tests._goldens import compare_or_update

def test_basic_project_sarif(sample_project_path, tmp_path):
    result = orchestrator.scan(sample_project_path)
    SARIFReporter().emit(result, tmp_path / "out.sarif")
    compare_or_update(
        actual=(tmp_path / "out.sarif").read_text(),
        golden=Path(__file__).parent / "goldens" / "basic_project_findings.sarif",
        normalizer=normalize_sarif,   # strips runtime timestamps, abs paths, version
    )
```

### 9.2 `tests/_goldens.py`

```python
import os
from pathlib import Path

def compare_or_update(actual: str, golden: Path, normalizer=lambda s: s):
    actual_norm = normalizer(actual)
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.write_text(actual_norm, encoding="utf-8")
        return
    if not golden.exists():
        raise AssertionError(f"Golden missing: {golden} (run with UPDATE_GOLDENS=1 to create)")
    expected = normalizer(golden.read_text(encoding="utf-8"))
    assert actual_norm == expected, f"Golden mismatch: {golden}"
```

Running `UPDATE_GOLDENS=1 pytest tests/integration` regenerates all goldens; diff must be human-reviewed before committing.

### 9.3 Normalizers

Each report format has a dedicated normalizer to strip variable parts:

- **SARIF**: replace `tool.driver.version` with `"TEST"`, strip `originalUriBaseIds[%SRCROOT%].uri` absolute path, remove any timestamps, sort arrays by stable keys.
- **JSON**: replace `ScanResult.project_root` with `"<ROOT>"`, strip `render_stats.duration_ms`, sort `findings` and `coverage` lists by deterministic keys.
- **Console**: strip ANSI codes, replace absolute paths.

Normalizers live in `tests/_normalizers.py`, unit-tested themselves.

### 9.4 Diff ergonomics

On mismatch, the failure message includes a unified diff (`difflib.unified_diff`) of the first 50 differing lines and the path to both actual and golden. Keeps CI logs actionable.

---

## 10. Test-plugin fixture (`tests/fixtures/test_plugin/`)

Tiny pip-installable package that registers a rule via entry points — used by SPEC-07's registry tests to verify third-party discovery.

```
test_plugin/
  pyproject.toml                        # [project.entry-points."dbt_coverage.rules"] test_rule = "test_plugin:TestRule"
  test_plugin/__init__.py               # class TestRule: id="T001"; ...
```

Installed via `pip install -e tests/fixtures/test_plugin` in CI before running SPEC-07 tests. Install can be gated on a pytest marker so local runs without the install skip the plugin test with a clear message.

---

## 11. Edge cases the harness itself must handle

| Case | Handling |
|---|---|
| Fixture paths contain spaces or unicode | Quoted in shell commands; `Path` handles transparently |
| Running tests in parallel (`pytest -n auto`) | Each test gets its own `tmp_path`; no shared mutable state in fixtures |
| Goldens drift due to Python version differences in dict ordering | Normalizers sort dict keys; `json.dumps(..., sort_keys=True)` |
| Goldens drift due to sqlglot version bump | Document upgrade cadence; bump sqlglot in a dedicated PR with golden regeneration |
| CI runs on Linux, dev runs on macOS — path separators | Normalizer forces POSIX for goldens |
| Coverage plugin inflates execution time | `--cov-fail-under` only in CI profile; local `pytest` opts out via `-o addopts=""` |
| Tests accidentally write outside `tmp_path` | `pytest-tmp-path` + cleanup fixtures catch this; CI checks `git status` clean at end |

---

## 12. Acceptance criteria

- [ ] `pytest` (from repo root) runs all unit + integration tests and produces ≥90% package coverage.
- [ ] `sample_dbt_project` renders and parses fully (no `render_uncertain`, no `parse_success=False`).
- [ ] End-to-end golden test (`test_basic_project_sarif`) passes with the initial committed golden.
- [ ] `UPDATE_GOLDENS=1 pytest` regenerates goldens deterministically (byte-identical on rerun with no code changes).
- [ ] `broken_project` fixture drives at least one test in SPEC-05, SPEC-06, SPEC-18, SPEC-12 each.
- [ ] `test_plugin` fixture installs cleanly via `pip install -e` and registers its rule.
- [ ] `ruff check tests/` clean.
- [ ] `mypy --strict` clean for fixture factories in `conftest.py` (shared factories are typed; test bodies can remain pragmatic).

---

## 13. Open questions

- Should we use `pytest-golden` or `syrupy` for snapshot testing instead of rolling our own? **Proposal:** roll our own — it's ~20 lines, no extra dep, and we need custom normalizers anyway. Revisit if snapshot count exceeds ~30.
- Should the sample project be a real-world realistic schema (orders/events/users) or a pathological "one of each rule" layout? **Proposal:** realistic — closer to real usage, goldens are more meaningful. Pathological cases go in targeted unit tests, not the sample.
- Should we vendor SARIF 2.1.0 schema or fetch at test time? **Proposal:** vendor (`schemas/sarif-2.1.0.json`) — CI reproducibility > staying current. Upgrade in a dedicated PR.
- Should `tests/fixtures/sample_dbt_project/` also have a `target/manifest.json` so phase-2 artifact-loader tests can reuse it? **Proposal:** yes — generate once via real `dbt compile` in a dev environment, commit the output. Phase-1 ignores it; phase-2 consumes it. Avoids maintaining two sample projects.
