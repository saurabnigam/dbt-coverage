# SPEC-09a — Coverage Calculator (test + doc only)

**Status:** draft
**Depends on:** SPEC-01, SPEC-03
**Blocks:** SPEC-11

---

## 1. Purpose

Compute two coverage dimensions from source-only inputs (no artifacts required in phase 1):

- **Test coverage** — fraction of models with ≥1 test (generic or singular) declared in schema.yml.
- **Doc coverage** — fraction of models with a non-empty `description` declared in schema.yml, plus per-column doc coverage across all declared columns.

These two dimensions validate the coverage pipeline end-to-end with only YAML introspection. `unit` and `column` dimensions are deferred to SPEC-09b (phase 2, requires artifacts + lineage).

---

## 2. Non-goals

- No `unit` (unit-test) coverage — needs dbt 1.8+ artifact introspection, phase 2.
- No `column` coverage — needs column lineage (SPEC-15), phase 2.
- No `pii` coverage — phase 2 security pack.
- No artifact-based coverage variants (e.g. "tests that actually ran in CI"). Phase 1 is source-declared only.
- No trend-over-time computation (historical coverage vs. current). Baseline diff (SPEC-19) handles that.

---

## 3. Module layout

```
src/dbt_coverage/coverage/
  __init__.py
  base.py                   # CoverageDimension protocol + helpers
  test_coverage.py          # compute_test_coverage()
  doc_coverage.py           # compute_doc_coverage()
  aggregator.py             # compute_all() orchestrator
```

---

## 4. API Surface

### 4.1 `base.py`

```python
from typing import Protocol, Literal
from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex

Dimension = Literal["test", "doc", "unit", "column", "pii"]

class CoverageDimensionFn(Protocol):
    def __call__(self, project: ProjectIndex) -> CoverageMetric: ...
```

### 4.2 `test_coverage.py`

```python
from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex

def compute_test_coverage(project: ProjectIndex) -> CoverageMetric:
    """
    For each model in project.models:
      covered := (yml_meta is not None) AND (
          any(col.tests) for col in yml_meta.columns
          OR len(yml_meta.model_tests) > 0        # singular/model-level tests
      )
    per_node[node_id] = (1, 1) if covered else (0, 1)
    total_covered = sum over models; total = len(models)
    ratio = covered / total (or 1.0 if total == 0)
    """
```

**Counting rules:**
- A model is "covered" if it has at least one test of any kind. One test is enough; we don't weight by test count.
- Tests declared on columns count (`unique`, `not_null`, `accepted_values`, `relationships`, custom).
- Model-level tests (`tests:` at the model block or in `unit_tests:` — though unit_tests count toward the "unit" dimension, not "test") count toward test coverage.
- Source tests (tests on `sources:` blocks) are scoped to sources, not models — they don't count here. Sources aren't part of the coverage denominator in phase 1.

**per_node semantics:** `tuple[int, int]` = `(covered_count, total_count)`. For test-coverage-per-model it's always `(0,1)` or `(1,1)` since each model is its own unit. The shape generalizes to doc coverage (where it's `(docd_cols, total_cols)` at finer granularity).

### 4.3 `doc_coverage.py`

```python
def compute_doc_coverage(project: ProjectIndex) -> CoverageMetric:
    """
    Doc coverage is a combined metric:
      - Model-level: model has non-empty `description`.
      - Column-level: each column has non-empty `description`.
    Both roll up into one CoverageMetric:
      covered = (models with description) + sum(cols with description)
      total   = len(models) + sum(total declared cols across all models)
    per_node[node_id] = (model_doc + col_docs_covered, 1 + total_cols)
    A model with no yml_meta contributes (0, 1) — it's undocumented and counts in total.
    """
```

**Why combine model + column into one metric:** keeps the dashboard simple. Users see one "doc" percentage. If we split it, we need two gate thresholds — more config surface for no clear win. Per-node breakdown retains granularity for anyone who wants it.

**Empty / whitespace-only descriptions count as missing.** `description: " "` → not covered.

**Models with no YAML at all:** contribute `(0, 1)` — the model counts as undocumented (model-level), and we cannot enumerate columns so we add 0 to the column total (can't discount a model for columns we don't know exist). Gate still penalizes via the model-level miss. This keeps the metric deterministic without needing the AST.

### 4.4 `aggregator.py`

```python
from dbt_coverage.core import CoverageMetric
from dbt_coverage.scanners import ProjectIndex

DIMENSIONS = {
    "test": compute_test_coverage,
    "doc":  compute_doc_coverage,
}

def compute_all(project: ProjectIndex, enabled: list[str] | None = None) -> list[CoverageMetric]:
    """
    Runs each enabled dimension (default: all keys in DIMENSIONS).
    Unknown dimension names in `enabled` are silently skipped (logged warning).
    Returns list ordered by enabled input order for deterministic reporting.
    """
```

---

## 5. Edge cases

| Case | Expected |
|---|---|
| Project has 0 models | `ratio = 1.0` (vacuously covered), `total = 0`, `per_node = {}` |
| Model with YAML but no columns and no model-level tests, no description | test: (0,1), doc: (0, 1+0) = (0,1) |
| Model with description but no columns declared | doc: (1, 1) |
| Column with `description: null` (YAML null) | Not covered |
| Column with `description: ""` | Not covered |
| Column with `description: "   "` (whitespace) | Not covered |
| Model-level test declared as singular (`tests:` block with an SQL file reference) | Covered for test dimension |
| Model referenced by source tests only | Not covered (source tests don't propagate) |
| Two schema.yml blocks pointing at same model | Merger in SPEC-03 already dedupes; coverage sees unified model |
| `yml_meta is None` (undocumented, untested) | test: (0,1); doc: (0,1) |
| Column list is duplicated in YAML (same col declared twice) | SPEC-03 dedupes; coverage sees one |

---

## 6. Ratio arithmetic

```
ratio = covered / total if total > 0 else 1.0
```

Float, not Decimal. Reported to 2 decimal places in console, full precision in JSON/SARIF. No clamping needed — `covered ≤ total` by construction.

**Why 1.0 on empty project:** a project with 0 models can't fail doc coverage, and `0/0` must be defined. The alternative (0.0) would be punitive and break gate logic on nascent projects.

---

## 7. Tests (`tests/unit/coverage/`)

### 7.1 `test_test_coverage.py`
- 3 models, 2 with ≥1 column test, 1 without → ratio 0.667, per_node correct.
- Model with model-level singular test but no column tests → covered.
- Model with `yml_meta=None` → not covered.
- Empty project → ratio 1.0, total 0.
- Test declared via dict form (`- unique: {config: {severity: warn}}`) → covered.

### 7.2 `test_doc_coverage.py`
- Model with description + 2/3 columns documented → (1+2, 1+3) = (3, 4), ratio 0.75.
- Whitespace-only description → not covered.
- Model with no YAML → (0, 1).
- Empty project → ratio 1.0.

### 7.3 `test_aggregator.py`
- `compute_all` returns 2 metrics when both dims enabled.
- `enabled=["test"]` → 1 metric.
- `enabled=["test", "bogus"]` → 1 metric, warning logged.
- Order of `enabled` preserved in output list.

**Coverage target:** 95%.

---

## 8. Acceptance criteria

- [ ] `compute_test_coverage(project)` and `compute_doc_coverage(project)` are pure functions of `ProjectIndex` (no I/O, no side effects beyond logging).
- [ ] Both return valid `CoverageMetric` instances — Pydantic validation passes.
- [ ] On `examples/basic_project/` returns documented expected values (fixtured in SPEC-13).
- [ ] `ruff`, `mypy --strict` clean.
- [ ] ≥95% coverage on `tests/unit/coverage/`.
- [ ] Execution <50ms on a 500-model synthetic project.

---

## 9. Open questions

- Should we introduce a "coverage weight" concept (critical models count 2×)? **Proposal:** no — model criticality is user-defined and orthogonal; bolt on in a later spec if requested.
- Report column doc coverage separately from model doc coverage? **Proposal:** phase-2 report formatter can split the `per_node` tuples to show both, but the gated metric stays combined.
- Phase 2 additions (tracked here for continuity, not implemented): `compute_unit_coverage` (requires manifest or YAML `unit_tests:` block discovery), `compute_column_coverage` (requires SPEC-15 lineage), `compute_pii_coverage` (requires meta.pii tag introspection).
