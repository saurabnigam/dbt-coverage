# dbt-coverage-lib (`dbtcov`)

A Data Quality Control Plane for Analytics Engineering — **JaCoCo + SonarQube
for dbt**. Unifies five signals (SQL quality, **test coverage**, **meaningful
test coverage**, **complexity-weighted coverage**, performance, refactor
candidates) and external tools (SQLFluff, dbt test results) into a single
CI-enforceable report that speaks **SARIF** and **JSON** natively.

## Built-in rules

| Rule  | Category     | Default Tier    | Description                                                    |
| ----- | ------------ | --------------- | -------------------------------------------------------------- |
| Q001  | QUALITY      | TIER_2_WARN     | `SELECT *` in non-source model or CTE                          |
| Q002  | QUALITY      | TIER_1_ENFORCED | Model missing primary-key test (`unique` + `not_null`)         |
| Q003  | QUALITY      | TIER_2_WARN     | High cyclomatic complexity (warn ≥ 15, block ≥ 30)             |
| Q004  | QUALITY      | TIER_2_WARN     | Model missing description in schema.yml                        |
| Q005  | QUALITY      | TIER_2_WARN     | Projection column missing from schema.yml `columns:`           |
| Q006  | QUALITY      | TIER_1_ENFORCED | Model name doesn't match its layer prefix (`stg_`, `fct_`, …)  |
| Q007  | QUALITY      | TIER_2_WARN     | Inconsistent column casing within a projection                 |
| P001  | PERFORMANCE  | TIER_1_ENFORCED | Cross-join / cartesian product without filter                  |
| P002  | PERFORMANCE  | TIER_2_WARN     | Non-sargable predicate (O(N) scan)                             |
| P003  | PERFORMANCE  | TIER_1_ENFORCED | Self-join with inequality-only predicate (O(N²))               |
| P004  | PERFORMANCE  | TIER_2_WARN     | Fully-unbounded window frame                                   |
| P005  | PERFORMANCE  | TIER_2_WARN     | `COUNT(DISTINCT …) OVER (…)`                                   |
| P006  | PERFORMANCE  | TIER_2_WARN     | Fan-out join on a non-unique key                               |
| P007  | PERFORMANCE  | TIER_2_WARN     | `ORDER BY` inside a CTE/subquery without `LIMIT`               |
| P008  | PERFORMANCE  | TIER_2_WARN     | Deep CTE chain (> `max_depth`)                                 |
| P009  | PERFORMANCE  | TIER_2_WARN     | Over-referenced view (promote to table/incremental)            |
| P010  | PERFORMANCE  | TIER_1_ENFORCED | Incremental model missing `unique_key` / `incremental_strategy`|
| R001  | REFACTOR     | TIER_1_ENFORCED | Near-duplicate models (MinHash + sqlglot diff)                 |
| R002  | REFACTOR     | TIER_2_WARN     | God model (too many CTEs + columns + complexity)               |
| R003  | REFACTOR     | TIER_2_WARN     | Single-use CTE (consider inlining)                             |
| R004  | REFACTOR     | TIER_2_WARN     | Dead CTE (defined but never referenced)                        |
| R005  | REFACTOR     | TIER_2_WARN     | Duplicate projection expression across ≥ N models              |
| R006  | REFACTOR     | TIER_2_WARN     | Duplicate `CASE` ladder across ≥ N models                      |
| A001  | ARCHITECTURE | TIER_1_ENFORCED | Layer violation (e.g. mart → staging)                          |
| A002  | ARCHITECTURE | TIER_2_WARN     | High fan-in (indegree > threshold)                             |
| A003  | ARCHITECTURE | TIER_1_ENFORCED | Direct `source()` bypass from intermediate/mart                |
| A004  | ARCHITECTURE | TIER_1_ENFORCED | Circular dependency in the DAG                                 |
| A005  | ARCHITECTURE | TIER_2_WARN     | Leaky abstraction (staging exposes raw column names)           |
| T001  | TESTING      | TIER_1_ENFORCED | Declared test didn't execute                                   |
| T002  | TESTING      | TIER_2_WARN     | Model without any unit test (dbt ≥ 1.8)                        |
| T003  | TESTING      | TIER_2_WARN     | Malformed unit test (missing `given` / `expect`)               |
| S001  | SECURITY     | TIER_1_ENFORCED | PII column projected without `mask_*` / `hash_*` macro         |
| S002  | SECURITY     | TIER_1_ENFORCED | Hard-coded secret / credential in SQL literal                  |
| G001  | GOVERNANCE   | TIER_2_WARN     | Model missing `meta.owner` / `meta.team`                       |
| G003  | GOVERNANCE   | TIER_1_ENFORCED | Waiver expired in `dbtcov.yml` overrides                       |
| SQLF.*| QUALITY      | TIER_2_WARN     | SQLFluff lint violations (via the sqlfluff adapter)            |

## Coverage dimensions (JaCoCo+Sonar style)

| Dimension           | What it measures                                                                       |
| ------------------- | -------------------------------------------------------------------------------------- |
| `test`              | % models with **any** declared test (SPEC-09a; back-compat).                           |
| `doc`               | Combined model+column documentation coverage.                                          |
| `test_meaningful`   | % models with ≥1 **logical** data test that **passed** (SPEC-22).                      |
| `test_weighted_cc`  | Coverage weighted by model complexity — heavy models need richer tests.                |
| `test_unit`         | % models with ≥1 `unit_test` (dbt ≥ 1.8); renders even at 0% so regressions are loud.  |
| `complexity`        | % models with cyclomatic complexity under `complexity.threshold_warn`.                 |

Tests are classified as `TRIVIAL` (`not_null`, `unique`), `STRUCTURAL`
(`accepted_values`, `relationships`, …), or `LOGICAL` (singular SQL, unit
tests, `dbt_expectations.*`, custom generics). Weights are configurable in
`dbtcov.yml → coverage.weights` and individual tests can be reclassified via
`coverage.test_overrides`.

Actual pass/fail data comes from dbt's `target/run_results.json`; when the
file is absent, the meaningful/weighted dimensions silently fall back to
declared-only mode.

## Quick start

```bash
pip install -e .[dev]
cd path/to/my/dbt/project
dbtcov init               # scaffold dbtcov.yml
dbtcov scan --path . --format console json sarif --out dbtcov-out
dbtcov models --results dbtcov-out/findings.json
dbtcov gate --results dbtcov-out/findings.json --path .
```

The repository also ships an onboarding helper:

```bash
bash scripts/onboard.sh --project-path .
```

## Typical workflow

```bash
# 1) Scan and emit artifacts
dbtcov scan --path . --format console json sarif --out dbtcov-out

# 2) Inspect per-model risk (worst first)
dbtcov models --results dbtcov-out/findings.json --sort score

# 3) Focus only on at-risk models
dbtcov models --results dbtcov-out/findings.json --min-score 70

# 4) Evaluate the gate from saved results
dbtcov gate --results dbtcov-out/findings.json --path .
```

### Render modes (SPEC-25)

`render.mode` controls how Jinja is handled:

- `AUTO` (default) — use `target/compiled/**` when present, otherwise fall
  back to MOCK. Best parse success rate.
- `COMPILED` — fail loud if `target/compiled` is missing; highest fidelity
  since dbt already rendered every macro and ref.
- `MOCK` — greenfield / offline mode. Only built-in dbt Jinja is
  recognised; custom macros mark the node `render_uncertain` (rules that
  need the AST skip with `RENDER_UNCERTAIN`).

CLI overrides: `--render-mode COMPILED` and `--compiled-dir path/to/compiled`.

### Baseline workflow (SPEC-31)

Capture a baseline once, then fail only on new violations:

```bash
# create/update .dbtcov/baseline.json
dbtcov baseline capture --path .

# compare current findings to baseline
dbtcov baseline diff --path .

# run scan against a specific baseline file
dbtcov scan --path . --baseline .dbtcov/baseline.json
```

### Waivers and baselines (SPEC-31)

Every SonarQube-style knob is driven by `dbtcov.yml`:

```yaml
overrides:
  - id: "WV-2026-001"
    waive: ["Q001"]
    models: ["fct_orders"]
    reason: "Star allowed for API contract; reviewed by @sonia."
    reviewer: "sonia"
    expires: "2026-09-30"
coverage:
  exemptions:
    test:
      - "models/legacy/**"
```

- Waived findings stay in JSON/SARIF with `suppressed=true` and the full
  `Suppression{source,reviewer,reason,expires}`. Console hides by
  default — use `dbtcov scan --show-suppressed` to reveal them.
- Expired waivers emit a fresh `G003` finding **and** re-activate the
  underlying rule.
- `dbtcov scan --baseline .dbtcov/baseline.json` stamps existing
  findings as `source=baseline` suppressions so only new violations
  count. Refresh with `dbtcov baseline capture`.

### Test kinds & execution tracking (SPEC-32)

The `dbt-test` adapter reads `target/manifest.json` + `target/run_results.json`
and classifies every test as `DATA` (classic generic/singular) or
`UNIT` (dbt 1.8+ `unit_tests`). Tests that didn't run are surfaced as
`T001 unexecuted test` (Tier-1 by default — CI should enforce, not
tolerate, skipped tests). Unit-test coverage rolls up as the
`test_unit` dimension.

### Skip-check transparency (SPEC-33)

Every rule that doesn't evaluate a given `(rule, node)` pair records a
`CheckSkip` with a typed `CheckSkipReason` (`parse_failed`,
`render_uncertain`, `adapter_missing`, `rule_disabled`, …). The console
prints a banner, the `dbtcov models` table gets a `Skips` column, and
SARIF emits `invocations[].toolExecutionNotifications`. Granularity is
tunable via `reports.skip_detail` (summary | aggregated | per_pair).

### Non-standard project layouts

By default dbtcov auto-discovers `dbt_project.yml` in the following order:

1. `<path>/dbt_project.yml`
2. `<path>/config/dbt_project.yml`
3. `<path>/conf/dbt_project.yml`
4. Walks up the tree from `<path>`.

When the config file lives in a subdirectory with relative paths like
`source-paths: ['../models']`, dbtcov computes the effective scan root
automatically and rewrites the paths. You can also point at the file
explicitly:

```bash
dbtcov scan --path . --project-config config/dbt_project.yml
```

If `dbt_project.yml` is unparseable (Git merge markers, duplicate keys,
etc.) dbtcov logs a WARN and proceeds on the conventional defaults
(`models/`, `tests/`, `macros/`, `target/`), recovering the project name
from `target/manifest.json` when available.

Outputs in `dbtcov-out/`:
- `findings.sarif` — upload to GitHub Code Scanning or any SARIF-aware UI.
  Includes `runs[0].properties.complexity` and `adapterInvocations`.
- `findings.json`  — canonical `ScanResult` payload (round-trips via Pydantic;
  carries `complexity`, `test_results`, `adapter_invocations`, `check_skip_summary`,
  and `model_summaries`).
- `coverage.json`  — coverage-only slice.
- console report to stdout (plus a Complexity panel and an Adapters panel).

### Ingesting external tool output

dbtcov speaks native JSON of the most common ecosystem tools through a
pluggable adapter framework (SPEC-21). Built-in adapters:

- `dbt-test` — reads `target/manifest.json` + `target/run_results.json` to
  enrich test coverage with real pass/fail status.
- `sqlfluff` — reads a `sqlfluff lint --format json` report and ingests each
  violation as a finding with rule-id `SQLF.<code>`.

CLI flags:

```bash
# Explicitly disable the dbt-test adapter (auto by default):
dbtcov scan --path . --no-dbt-artifacts

# Point at an artifact directory other than target/:
dbtcov scan --path . --dbt-artifacts-dir build/dbt

# Supply a pre-existing sqlfluff report:
dbtcov scan --path . --sqlfluff-report .dbtcov/sqlfluff.json

# Run sqlfluff inline (requires sqlfluff on PATH):
dbtcov scan --path . --run-sqlfluff

# Generic adapter controls (SPEC-21 §8), apply to any built-in or plugin adapter:
dbtcov scan --path . --adapter sqlfluff --adapter-mode sqlfluff=run
dbtcov scan --path . --adapter-report sqlfluff=.dbtcov/sqlfluff.json
dbtcov scan --path . --no-adapter dbt-test

# List every registered adapter (built-in + plugin) and exit:
dbtcov scan --list-adapters
```

## Design principles (non-negotiable)

1. Never fully execute dbt — simulate structure, not data.
2. Preserve semantic identity of dbt constructs (`ref`, `source`) through
   Jinja rendering via sentinel strings (`__REF_orders__`, `__SRC_raw_events__`).
3. Fail gracefully — every stage (parser, adapter, rule) has a "mark
   uncertain, keep going" fallback; one failing adapter never fails the scan.
4. Incremental + baseline-aware from v1 (baseline diff lands in SPEC-18).
5. Tiered rules — only Tier-1 blocks CI; Tier-2 warns.

## Architecture

```
                              dbtcov scan
                                  │
 source files ─► scanner ─► jinja render (MOCK) ─► sqlglot parse
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                    ProjectIndex ParsedNode Complexity
                                          │          │          │
                                          └────► AnalysisGraph ◄┘
                                                     │
                                ┌────────────────────┼─────────────────────┐
                                ▼                    ▼                     ▼
                         Rule Engine            Coverage Calc          Adapters
                           (Q/P/R)       (test/doc/meaningful/cc/complexity)
                                │                                          │
                                ▼                                          ▼
                             findings  ◄──── merge/dedup ────► findings + test_results
                                               │
                                               ▼
                                          ScanResult
                                               │
                                 ┌─────────────┼─────────────┐
                                 ▼             ▼             ▼
                               SARIF         JSON        Console
                                               │
                                               ▼
                                      Quality Gate (→ exit code)
```

## Development

```bash
pip install -e .[dev]
pytest                    # run unit + integration tests
ruff check src tests
mypy src/dbt_coverage
```

Regenerate test goldens:
```bash
UPDATE_GOLDENS=1 pytest tests/integration
```

See `docs/specs/` for the complete design documents (SPEC-01 through SPEC-33):

- SPEC-25 — COMPILED render mode
- SPEC-26 — Refactor rule pack (R002–R006)
- SPEC-27 — Architecture rule pack (A001–A005) + layer classifier
- SPEC-28 — Performance rule pack (P002–P010) with Big-O hints
- SPEC-29 — Quality rule pack extensions (Q004–Q007)
- SPEC-30 — Security & Governance pack (S001, S002, G001, G003)
- SPEC-31 — Waivers, overrides, and baselines
- SPEC-32 — Test kinds and execution tracking
- SPEC-33 — Check-skip tracking

# dbt-coverage
