# SPEC-32 — Test kinds + execution tracking

## 1. Problem

dbt 1.8 introduced a second test concept: *unit tests* (`unit_tests:` block with `given`/`expect`) alongside *data tests* (`not_null`, `unique`, generic, singular). The existing `TestResult` model doesn't distinguish them, so a model with only `not_null` tests and no logic-level coverage looks identical to a model with unit tests. In addition, tests that are **defined in manifest but never executed** (partial `dbt test --select` runs, missing `run_results.json`) silently disappear from the report — a blind spot.

## 2. Goals

- Segregate data tests from unit tests via a new `TestKind` enum.
- Diff `manifest.json` against `run_results.json` so unexecuted tests become first-class `TestResult` entries with `executed=False`.
- Add a dedicated `test_unit` coverage dimension.
- Ship three new rules: T001 (unexecuted), T002 (no unit tests), T003 (malformed unit test).
- CI-first posture: T001 defaults to **Tier 1 / ERROR**; escape hatches live in `dbtcov.yml`.

## 3. Core types

```python
class TestKind(StrEnum):
    DATA = "data"          # manifest.resource_type == "test" (generic + singular)
    UNIT = "unit"          # manifest.resource_type == "unit_test"
    UNKNOWN = "unknown"

class TestResult(BaseModel):
    # existing fields...
    kind: TestKind = TestKind.UNKNOWN
    executed: bool = True
```

`TestKind` is orthogonal to `TestClass {TRIVIAL, STRUCTURAL, LOGICAL, UNKNOWN}` — kind captures *mechanism*, class captures *semantic weight*.

`ModelSummary` gains `data_test_count: int`, `unit_test_count: int`, `tests_not_run_count: int`.

## 4. Adapter changes

[src/dbt_coverage/adapters/dbt_test/adapter.py](../../src/dbt_coverage/adapters/dbt_test/adapter.py) changes:

1. Classify during manifest walk: `resource_type == "test"` → `DATA`; `resource_type == "unit_test"` → `UNIT`.
2. Iterate both `nodes` and `unit_tests` top-level manifest collections (dbt 1.8+).
3. Diff the set of manifest `unique_id`s against the set of observed `unique_id`s in run_results; for every missing `unique_id` emit a `TestResult(status=UNKNOWN, executed=False, kind=…)`.
4. Handle the case where no `run_results.json` exists at all — every manifest test becomes `executed=False`.
5. Expose `manifest.metadata.dbt_version` on the AdapterResult so downstream can gate unit-test rendering.

## 5. Coverage dimension

[src/dbt_coverage/coverage/aggregator.py](../../src/dbt_coverage/coverage/aggregator.py) gains:

```python
CoverageDimension.TEST_UNIT = "test_unit"
```

Calculation: `models_with_unit_test / total_models`.

Rendering: always emit, but on dbt < 1.8 the dimension shows `0 / N` with a note `"dbt < 1.8 — unit_tests: not supported"` (console) / `"note": "dbt_version_below_1_8"` (JSON).

Existing `test_meaningful` / `test_weighted_cc` scope to `TestKind.DATA` only so unit tests don't double-count.

## 6. Rules

### T001 test defined but not executed

- **Tier**: `TIER_1_ENFORCED`, **Severity**: `MAJOR`, **Category**: `COVERAGE`.
- Fires once per `TestResult` with `executed=False`.
- Message: *"Test `{test_name}` was defined in manifest but did not execute. Attach run_results.json from a full `dbt test` run or add to dbtcov.yml `overrides:`."*
- Points at the manifest-derived `original_file_path`.

### T002 model has no unit tests

- **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Per-model. Fires only when manifest `dbt_version >= 1.8`.
- Fires when `ModelSummary.unit_test_count == 0`.
- Auto-suppressed for models matching `testing.unit_tests.exempt` globs.

### T003 malformed unit test

- **Tier**: `TIER_2_WARN`, **Severity**: `MAJOR`.
- Fires when a manifest `unit_tests.*` entry is missing `given`, missing `expect`, or has an empty `expect.rows` list.

## 7. Gate config

```yaml
gate:
  thresholds:
    coverage:
      test_unit: 0                # default 0 (no enforcement)
    testing:
      unexecuted_tests_max: 0     # default 0: any unexecuted test fails gate

testing:
  unit_tests:
    exempt: ["stg_source_copy_*", "seeds/**"]
```

## 8. Reporters

- **Console**: the existing test summary becomes a 2×5 grid:
  - rows: `PASS / FAIL / ERROR / SKIP / NOT_EXECUTED`
  - cols: `DATA | UNIT`
- **`dbtcov models`**: new `Unit` column (`✓` / `✗` / `—`).
- **JSON**: `TestResult.kind` and `TestResult.executed` surface via Pydantic.
- **SARIF**: T001 findings flow as normal results; waivers from SPEC-31 apply uniformly.

## 9. Tests

- Adapter classification tests: every `resource_type` → correct `TestKind`.
- Unexecuted-diff tests: manifest has N tests, run_results has M → N-M `executed=False` entries.
- Coverage dimension tests: zero on dbt < 1.8, computes correctly on dbt ≥ 1.8.
- One unit test per rule (T001, T002, T003).
- Integration: full `dbt test` → 0 unexecuted, gate passes; partial `dbt test --select` → unexecuted > 0, gate fails on T001.
