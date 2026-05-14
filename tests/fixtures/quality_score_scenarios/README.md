# Quality Score Scenarios — Fixture Project

This fixture dbt project provides **one model per quality score test scenario**.
Each model's SQL and `_models.yml` entry is deliberately crafted so that its
expected quality score (and coverage metrics) can be verified by reading the files,
then confirmed by running `dbtcov scan`.

## Quick Start

```bash
# From the repo root
source .venv/bin/activate
dbtcov scan \
  --project-dir tests/fixtures/quality_score_scenarios \
  --report-format console
```

---

## Scenario Reference

| Model | Expected Score | Penalties Applied |
|---|---|---|
| `model_perfect` | **100** | None — full docs, logical test, no violations |
| `model_no_test` | **75** | `no_test=25` — documented but zero tests declared |
| `model_no_doc` | **85** | `doc=15` — tests exist but no model description |
| `model_no_test_no_doc` | **60** | `no_test=25` + `doc=15` |
| `model_trivial_tests_only` | **100** | `no_test=0` (YAML tests exist) — `not_null`/`unique` are TRIVIAL but don't trigger no_test penalty |
| `model_partial_doc` | **91** | `doc=9` — model has description but only 1 of 4 columns documented → `doc_ratio=0.4` → `round(0.6×15)=9` |
| `model_with_tier1_violations` | **97** | Q001 (SELECT \*) fires as tier2 → `tier2=3` |
| `model_high_complexity` | **94** | Q001 (SELECT \*) + Q003 (CC=17 ≥ 15) both tier2 → `tier2=3+3=6` |

> **Note**: G001, Q002, Q004, R003 are disabled in `dbtcov.yml` to keep these scenarios
> focused on the specific penalties each model illustrates. Q001 is a tier-2 rule by default
> (not tier-1), so the violation penalty is 3 per unique rule ID (not 10).

---

## Scoring Formula (default `ScoringConfig`)

```
score = 100
      - no_test_penalty          (25 if zero tests)
      - doc_penalty              (round((1 - doc_ratio) × 15), max 15)
      - tier1_penalty            (min(40, unique_tier1_rules × 10))
      - tier2_penalty            (min(20, unique_tier2_rules × 3))
      - unexec_penalty           (min(15, unexecuted_tests × 5))
      - parse_penalty            (10 if parse failed; 5 if render uncertain)
      - skip_penalty             (min(5, skipped_checks)  — only when parse ok)

score = max(0, score)
```

---

## Coverage Dimensions (separate from quality score)

| Dimension | Description |
|---|---|
| `test` | % of models with ≥1 test declared in YAML |
| `doc` | % of models with a model-level description |
| `test_meaningful` | % of models with ≥1 LOGICAL test (not just `not_null`/`unique`) |
| `test_unit` | % of models with ≥1 dbt unit test |
| `test_weighted_cc` | Complexity-weighted test coverage |
| `complexity` | % of models with cyclomatic complexity ≤ `threshold_warn` |

---

## How Test Classification Works

| Test kind (string) | Class | Weight |
|---|---|---|
| `not_null`, `unique` | TRIVIAL | 0.0 |
| `accepted_values`, `relationships` | STRUCTURAL | 0.25 |
| `singular`, `unit_test`, custom | LOGICAL | 1.0 |

Only LOGICAL-weight tests contribute to `test_meaningful` coverage.
Only `kind=UNIT` tests contribute to `test_unit` coverage.

---

## Corresponding Unit Tests

Each scenario maps directly to one or more tests in
[`tests/unit/test_quality_score.py`](../../unit/test_quality_score.py):

| Scenario | Unit test method |
|---|---|
| `model_perfect` | `TestQualityScore.test_score_100_perfect` |
| `model_no_test` | `TestQualityScore.test_score_75_no_test_penalty` |
| `model_no_doc` | `TestQualityScore.test_score_85_no_doc_penalty` |
| `model_no_test_no_doc` | `TestQualityScore.test_score_57_no_test_no_doc_one_tier2`, `TestMultiModelSummaries.test_two_models_independent_scores` |
| `model_trivial_tests_only` | `TestMeaningfulTestCoverage.test_trivial_tests_do_not_count` |
| `model_partial_doc` | `TestQualityScore.test_score_partial_doc_penalty_scales_linearly` |
| `model_with_tier1_violations` | `TestViolationsImpact.test_single_tier1_deducts_10`, `test_tier1_cap_at_40` |
| `model_high_complexity` | `TestComplexityCoverage.test_high_cc_model_is_not_covered`, `TestQualityScore.test_score_floors_at_zero_when_penalties_exceed_100` |
