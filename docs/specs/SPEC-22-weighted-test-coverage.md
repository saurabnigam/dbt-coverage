# SPEC-22 — Weighted & Meaningful Test Coverage

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-01, SPEC-09a (existing test/doc coverage), SPEC-19 (ComplexityMetrics), SPEC-21 (Adapter framework, `TestResult`)
**Blocks:** none (consumed by reporters + quality gate directly)

---

## 1. Purpose

The existing `test` coverage dimension (SPEC-09a) treats all tests equally: a model with only `not_null(id)` counts as "covered". That's the same fallacy as a Java project with 100% line coverage from trivial getters — it gives false confidence.

This spec introduces two new dimensions that map directly to JaCoCo+Sonar practice:

- **`test_meaningful`** — fraction of models with at least one *logical-weight* test that **passed** (when test-run evidence is available via the adapter framework).
- **`test_weighted_cc`** — coverage weighted by model complexity. A heavy model with only a `not_null` scores near zero; a simple model with a rich singular test scores high.

Both dimensions consume the `TestResult` stream emitted by **any** adapter (SPEC-21). There is zero hardcoded dependency on dbt-specific artefacts in this spec — the dbt-test adapter (SPEC-23) is one producer; sqlfluff-test, custom test runners, Elementary, unit-test frameworks are all peers.

The existing `test` dimension remains unchanged so current CI gates keep working.

---

## 2. Non-goals

- Not a replacement for the existing `test` dimension. It stays; some users have gates on it.
- Not a column-level coverage metric (SPEC-09b handles `column` coverage separately).
- Not a statistical effectiveness score (mutation-style). Weights are static, not empirically derived.
- No custom test authoring support. Weights classify *existing* tests by kind; they don't propose new tests.

---

## 3. Module layout

```
src/dbt_coverage/coverage/
  test_classifier.py                # classify a TestResult / manifest test node -> TestClass + weight
  test_meaningful_coverage.py       # computes dimension="test_meaningful"
  test_cc_weighted_coverage.py      # computes dimension="test_weighted_cc"
  complexity_metric.py              # computes dimension="complexity" (cross-ref SPEC-19 §7)
  aggregator.py                     # EXISTING; extended: register three new dimensions
```

---

## 4. Data model

### 4.1 `TestClass`

```python
from enum import StrEnum

class TestClass(StrEnum):
    TRIVIAL = "TRIVIAL"          # structural NOT NULL / UNIQUE — schema assertions
    STRUCTURAL = "STRUCTURAL"    # relationships, accepted_values, uniq-combos — shape checks
    LOGICAL = "LOGICAL"          # singular SQL, unit_tests, custom generics, dbt_expectations
    UNKNOWN = "UNKNOWN"
```

### 4.2 Weight table (default, configurable)

| Class      | Weight | Default members |
|------------|--------|-----------------|
| TRIVIAL    | 0.0    | `not_null`, `unique` |
| STRUCTURAL | 0.25   | `accepted_values`, `relationships`, `unique_combination_of_columns`, `dbt_utils.at_least_one`, `dbt_utils.not_constant`, `dbt_utils.not_empty_string` |
| LOGICAL    | 1.0    | singular SQL tests (`data_test_type == "singular"`), `unit_test` (dbt 1.8+ `unit_tests:` blocks), anything under `dbt_expectations.*`, `dbt_utils.expression_is_true`, all unrecognised generics |
| UNKNOWN    | 0.0    | classifier failed or adapter omitted `test_kind` |

Pydantic config (extends `DbtcovConfig.coverage`):

```python
class WeightTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trivial: float = Field(default=0.0, ge=0.0, le=1.0)
    structural: float = Field(default=0.25, ge=0.0, le=1.0)
    logical: float = Field(default=1.0, ge=0.0, le=1.0)
    unknown: float = Field(default=0.0, ge=0.0, le=1.0)


class TestOverrides(BaseModel):
    """Per-test-kind reclassification."""
    model_config = ConfigDict(extra="forbid")
    logical: list[str] = []       # exact match on TestResult.test_kind or prefix with "*" (e.g. "dbt_expectations.*")
    structural: list[str] = []
    trivial: list[str] = []
```

Config surface:

```yaml
coverage:
  test_meaningful: { min: 0.50 }
  test_weighted_cc: { min: 0.60 }
  weights:
    trivial: 0.0
    structural: 0.25
    logical: 1.0
  test_overrides:
    structural:
      - "my_org.foreign_key_soft"
    logical:
      - "dbt_expectations.*"
```

### 4.3 `CoverageMetric.dimension` Literal is extended

SPEC-01 §4.3 gains three new literals:

```python
dimension: Literal["test", "doc", "unit", "column", "pii", "test_meaningful", "test_weighted_cc", "complexity"]
```

---

## 5. Classifier

### 5.1 Inputs & outputs

```python
def classify(
    test_kind: str,
    overrides: TestOverrides,
) -> tuple[TestClass, float]:
    """Return class + weight from the configured weight table."""
```

### 5.2 Algorithm

```python
_DEFAULT_TRIVIAL = frozenset({"not_null", "unique"})
_DEFAULT_STRUCTURAL = frozenset({
    "accepted_values", "relationships", "unique_combination_of_columns",
    "dbt_utils.at_least_one", "dbt_utils.not_constant", "dbt_utils.not_empty_string",
})

def classify(test_kind, overrides, weights):
    k = test_kind or ""
    # 1. Overrides win, checked in order logical > structural > trivial so explicit wins.
    for cls in (TestClass.LOGICAL, TestClass.STRUCTURAL, TestClass.TRIVIAL):
        if _matches(k, getattr(overrides, cls.name.lower())):
            return cls, _weight(weights, cls)

    # 2. Defaults.
    if k in _DEFAULT_TRIVIAL:      return TestClass.TRIVIAL, weights.trivial
    if k in _DEFAULT_STRUCTURAL:   return TestClass.STRUCTURAL, weights.structural
    if k == "":                    return TestClass.UNKNOWN, weights.unknown

    # 3. Everything else is logical (singular SQL, unit_test, dbt_expectations.*, custom generics).
    return TestClass.LOGICAL, weights.logical


def _matches(k: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatchcase
    return any(fnmatchcase(k, p) for p in patterns)
```

The `_DEFAULT_*` sets are *frozen* module constants; users change behaviour via `overrides`, never by mutating defaults.

---

## 6. `test_meaningful` dimension

### 6.1 Definition

```
test_meaningful = |{ model m : ∃ TestResult t on m where weight(classify(t.test_kind)) >= logical_threshold AND t.status == PASS (or UNKNOWN if no run_results were loaded) }| / |models|
```

- `logical_threshold` defaults to `weights.logical` (1.0), so only logical tests satisfy by default.
- If the `test_results` stream is empty (no adapter produced one) the dimension falls back to the SPEC-09a "declared tests only" mode, using `status=UNKNOWN` as a pass-equivalent. This keeps dbtcov usable without `run_results.json` present.
- `per_node[m] = (1_if_covered, 1)`.

### 6.2 Algorithm (`coverage/test_meaningful_coverage.py`)

```python
def compute_test_meaningful_coverage(
    parsed_nodes: dict[str, ParsedNode],
    test_results: list[TestResult],
    cfg: "DbtcovConfig",
) -> CoverageMetric:
    weights = cfg.coverage.weights
    overrides = cfg.coverage.test_overrides
    logical_threshold = weights.logical

    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in test_results)

    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n)}
    covered: set[str] = set()

    for tr in test_results:
        if tr.model_unique_id not in model_ids:
            continue
        _, w = classify(tr.test_kind, overrides, weights)
        if w < logical_threshold:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        covered.add(tr.model_unique_id)

    total = len(model_ids)
    per_node = {m: (1 if m in covered else 0, 1) for m in model_ids}
    return CoverageMetric(
        dimension="test_meaningful",
        covered=len(covered),
        total=total,
        ratio=(len(covered) / total) if total else 0.0,
        per_node=per_node,
    )
```

---

## 7. `test_weighted_cc` dimension

### 7.1 Definition

```
            Σ_m  best_weight(m) · cc(m)
weighted =  --------------------------
                  Σ_m  cc(m)
```

- `best_weight(m)` = max weight across all passing (or all, if no status) tests attached to `m`, clamped to `[0, 1]`.
- `cc(m)` = `ComplexityMetrics.cc` from SPEC-19; if a model has no metrics, use 1 (neutral).
- Result ∈ `[0, 1]`.
- `per_node[m] = (round(best_weight(m) * cc(m)), cc(m))`.

Intuition: a 30-CC model with only `not_null` (weight 0) contributes 0 to the numerator but 30 to the denominator — hammering the score. A 3-CC stg model with one singular test scores full weight. A 15-CC mart with both `not_null` (0) and one logical singular (1) takes the **best** weight per model (= 1), so it's fully covered.

### 7.2 Algorithm (`coverage/test_cc_weighted_coverage.py`)

```python
def compute_test_cc_weighted_coverage(
    parsed_nodes: dict[str, ParsedNode],
    complexity: dict[str, ComplexityMetrics],
    test_results: list[TestResult],
    cfg: "DbtcovConfig",
) -> CoverageMetric:
    weights = cfg.coverage.weights
    overrides = cfg.coverage.test_overrides
    have_status = any(tr.status is not TestStatus.UNKNOWN for tr in test_results)

    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n)}
    cc_by_model = {m: (complexity[m].cc if m in complexity else 1) for m in model_ids}

    best_w: dict[str, float] = {m: 0.0 for m in model_ids}
    for tr in test_results:
        if tr.model_unique_id not in model_ids:
            continue
        if have_status and tr.status is not TestStatus.PASS:
            continue
        _, w = classify(tr.test_kind, overrides, weights)
        if w > best_w[tr.model_unique_id]:
            best_w[tr.model_unique_id] = w

    num = sum(best_w[m] * cc_by_model[m] for m in model_ids)
    den = sum(cc_by_model.values()) or 1   # avoid /0

    per_node = {
        m: (int(round(best_w[m] * cc_by_model[m])), cc_by_model[m])
        for m in model_ids
    }
    return CoverageMetric(
        dimension="test_weighted_cc",
        covered=int(round(num)),
        total=int(den),
        ratio=num / den,
        per_node=per_node,
    )
```

Note: `covered` and `total` are integers (required by `CoverageMetric`), so we round the numerator and denominator sums. This is display-only; `ratio` is computed from the real (float) values **before** rounding, so the `_ratio_matches` validator (SPEC-01 §4.3) must allow small drift — we relax it to `abs(ratio - covered/total) < 0.05` for dimensions in `{test_weighted_cc}`. Alternative considered: change `CoverageMetric.covered/total` to floats — rejected because it ripples into baseline JSON, SARIF, existing gates.

**Refined decision:** leave `CoverageMetric` integer-only. Store the float `ratio` as source of truth; `covered = int(round(num))`, `total = int(round(den))`. Extend the `_ratio_matches` validator to accept either exact match *or* `dimension` being in a `_APPROX_RATIO_DIMENSIONS = {"test_weighted_cc"}` allowlist.

---

## 8. `complexity` dimension

Mechanics defined in SPEC-19 §7. Algorithm (`coverage/complexity_metric.py`):

```python
def compute_complexity_summary(
    parsed_nodes: dict[str, ParsedNode],
    complexity: dict[str, ComplexityMetrics],
    cfg: "DbtcovConfig",
) -> CoverageMetric:
    threshold = cfg.complexity.threshold_warn
    model_ids = {nid for nid, n in parsed_nodes.items() if _is_model(n)}
    under = {m for m in model_ids if (complexity.get(m).cc if m in complexity else 1) <= threshold}
    total = len(model_ids)
    per_node = {m: (1 if m in under else 0, 1) for m in model_ids}
    return CoverageMetric(
        dimension="complexity",
        covered=len(under),
        total=total,
        ratio=(len(under) / total) if total else 0.0,
        per_node=per_node,
    )
```

---

## 9. Aggregator wiring

`coverage/aggregator.py` gains:

```python
DIMENSIONS = {
    "test":              lambda ctx: compute_test_coverage(ctx.parsed_nodes, ctx.declared_tests),
    "doc":               lambda ctx: compute_doc_coverage(ctx.parsed_nodes, ctx.yaml_index),
    "test_meaningful":   lambda ctx: compute_test_meaningful_coverage(ctx.parsed_nodes, ctx.test_results, ctx.config),
    "test_weighted_cc":  lambda ctx: compute_test_cc_weighted_coverage(ctx.parsed_nodes, ctx.complexity, ctx.test_results, ctx.config),
    "complexity":        lambda ctx: compute_complexity_summary(ctx.parsed_nodes, ctx.complexity, ctx.config),
}
```

`AggregatorContext` (existing) extends with:

```python
@dataclass
class AggregatorContext:
    parsed_nodes: dict[str, ParsedNode]
    declared_tests: ...
    yaml_index: ...
    test_results: list[TestResult]
    complexity: dict[str, ComplexityMetrics]
    config: DbtcovConfig
```

Enabled dimensions remain controlled by `cfg.coverage.dimensions` (which defaults to all known keys). Users can disable any dimension by listing only what they want.

---

## 10. Gate integration

`quality_gates.evaluate` (SPEC-11) already iterates over `cfg.coverage`. No code change — users simply add:

```yaml
coverage:
  test:            { min: 0.80 }
  test_meaningful: { min: 0.50 }
  test_weighted_cc:{ min: 0.60 }
  complexity:      { min: 0.90 }
```

---

## 11. Failure modes

| Case | Behavior |
|---|---|
| No adapters produced test_results | `test_meaningful` and `test_weighted_cc` fall back to declared-only mode (`have_status=False` → status filter skipped). |
| `test_results` contains entries with `model_unique_id=None` | Skipped (source/seed tests don't count toward model coverage). |
| Unknown `test_kind` string | Classified as LOGICAL by default (§5.2 step 3). Users can reclassify via overrides. |
| Complexity map empty (SPEC-19 not run) | `test_weighted_cc` uses `cc=1` for every model → becomes ≈ meaningful ratio. |
| All models have `cc=1` | Denominator = number of models; ratio still well-defined. |
| `weights.logical < weights.structural` | Accepted (unusual but valid); doc note that it inverts precedence. |
| User override and default collide (`trivial: ["not_null"]` overridden to `logical`) | Override wins. |
| `total=0` (empty project) | All three dimensions return `covered=0, total=0, ratio=0.0` per SPEC-01 convention. |
| Adapter emits duplicate TestResult (same `test_name`) | Dedup by `(test_name, model_unique_id)` before classification — keep the worst status (FAIL > ERROR > SKIPPED > PASS > UNKNOWN). |

---

## 12. Tests (`tests/unit/coverage/`)

### 12.1 `test_classifier.py`
- Each default in the weight table → expected class + weight.
- Unknown kind → LOGICAL.
- Empty kind → UNKNOWN.
- Override `logical: ["my_org.*"]` reclassifies `my_org.foo` → LOGICAL.
- Override + default collision → override wins.
- Case-sensitive match (glob).

### 12.2 `test_meaningful_coverage.py`
- Model with one passing singular test → covered.
- Model with one passing `not_null` only → not covered.
- Model with one failing singular → not covered (when statuses present).
- Empty test_results + 3 models all declaring a singular → covered (declared-only fallback).
- Test with `model_unique_id` not in parsed_nodes → ignored.
- `per_node` tuple correctness.

### 12.3 `test_cc_weighted_coverage.py`
- Two models: CC=3 with singular (best_w=1), CC=30 with not_null only (best_w=0). Numerator=3, denominator=33 → ratio ≈ 0.091. `covered=3, total=33`.
- All models fully covered → ratio=1.0.
- No complexity map → defaults to cc=1 per model → equals meaningful ratio.
- Ratio validator tolerates the small rounding gap (§7.2).

### 12.4 `test_complexity_metric.py`
- 10 models, 3 over threshold 15 → covered=7, total=10, ratio=0.7.
- Empty parsed_nodes → covered=0, total=0, ratio=0.0.
- Missing complexity entry → treated as cc=1 → under threshold → covered.

### 12.5 `test_aggregator_wiring.py`
- `DIMENSIONS` contains the three new keys.
- Aggregator context threads test_results + complexity through.
- Dimensions disabled via `cfg.coverage.dimensions = ["test"]` produce only the one metric.

---

## 13. Acceptance criteria

- [ ] `TestClass`, classifier, three new coverage functions, and `WeightTable` / `TestOverrides` configs exported.
- [ ] `CoverageMetric.dimension` Literal includes the three new members.
- [ ] `_ratio_matches` validator allowlist honoured for `test_weighted_cc`.
- [ ] Existing `test` / `doc` coverage outputs byte-identical to pre-change (regression guard).
- [ ] Gate evaluation recognises the new dimensions without changes to `quality_gates.evaluate`.
- [ ] All tests in §12 pass with ≥95% line coverage on the new modules.
- [ ] `ruff` + `mypy --strict` clean.

---

## 14. Open questions

- Should the weight table be allowed per-directory (`marts/` vs `staging/`)? *Proposal: defer — overrides already give a reclassification lever; per-path weights add one more dimension of confusion.*
- Should failing tests still contribute a small weight (e.g. 0.1) to acknowledge the author's effort? *Proposal: no — JaCoCo parallel is that a failing assertion doesn't count as covered. Consistent.*
- Should we expose the per-model `best_weight` on `ScanResult` for reporters to display a "test depth" column? *Proposal: not in v1. Reporters can reconstruct from `per_node` if needed; adding a new top-level field has baseline implications.*
