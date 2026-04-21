# dbt-coverage-lib - Low-Level Design

## 1. Core Domain Objects

```mermaid
classDiagram
    class ScanResult {
      +findings: list[Finding]
      +coverage: list[CoverageMetric]
      +model_summaries: list[ModelSummary]
      +project_root: Path
      +project_name: str?
      +dbt_version_detected: str?
      +dialect: str
      +render_stats: RenderStats
      +scan_duration_ms: int
      +schema_version: int
      +complexity: dict[str, ComplexityMetrics]
      +test_results: list[TestResult]
      +adapter_invocations: list[AdapterInvocation]
      +check_skip_summary: CheckSkipSummary
      +check_skips_aggregated: list[AggregatedCheckSkip]
      +check_skips: list[CheckSkip]
    }

    class Finding {
      +rule_id: str
      +severity: Severity
      +category: Category
      +type: FindingType
      +tier: Tier
      +confidence: float
      +message: str
      +file_path: Path
      +line: int
      +column: int
      +node_id: str?
      +fingerprint: str
      +is_new: bool
      +suppressed: bool
      +suppression: Suppression?
      +compiled_path: Path?
    }

    class CoverageMetric {
      +dimension: Literal[test,doc,unit,column,pii,test_meaningful,test_weighted_cc,test_unit,complexity]
      +covered: int
      +total: int
      +ratio: float
      +per_node: dict[str, tuple[int,int]]
      +notes: list[str]
    }

    class RenderStats {
      +total_files: int
      +rendered_mock: int
      +rendered_partial: int
      +rendered_compiled: int
      +render_uncertain: int
      +parse_success: int
      +parse_failed: int
    }

    class ModelSummary {
      +node_id: str
      +name: str
      +file_path: str
      +parse_success: bool
      +render_uncertain: bool
      +test_covered: bool
      +doc_ratio: float
      +tier1_rules: list[str]
      +tier2_rules: list[str]
      +score: int
      +data_test_count: int
      +unit_test_count: int
      +tests_not_run_count: int
      +waived_count: int
      +skip_count: int
    }

    class CheckSkip {
      +rule_id: str
      +node_id: str?
      +reason: CheckSkipReason
      +details: str?
    }

    class CheckSkipSummary {
      +total_skips: int
      +attempted_checks: int
      +effective_coverage_pct: float
      +by_reason: dict[CheckSkipReason, int]
      +by_rule: dict[str, int]
      +affected_nodes: int
    }

    ScanResult --> Finding
    ScanResult --> CoverageMetric
    ScanResult --> RenderStats
    ScanResult --> ModelSummary
    ScanResult --> CheckSkip
    ScanResult --> CheckSkipSummary
```

## 2. Orchestrator Implementation Flow

Main entrypoint: `dbt_coverage.cli.orchestrator.scan`.

Algorithm shape:
1. Resolve project root and load project info.
2. Load and merge config (file + CLI overrides).
3. Resolve dialect (`config.dialect` then adapter-derived fallback).
4. Scan sources to build `ProjectIndex`.
5. Select renderer (AUTO/MOCK/COMPILED), render, parse.
6. Build graph and complexity map.
7. Run adapters to collect findings, tests, and invocation metadata.
8. Discover/apply rule overrides; execute rule engine with skip tracking.
9. Merge rule + adapter findings and sort deterministically.
10. Apply waivers and baseline suppressions.
11. Compute coverage dimensions.
12. Build per-model summaries (`_build_model_summaries`).
13. Compute render stats and skip report (`_build_skip_report`).
14. Assemble `ScanResult`.

```mermaid
flowchart TD
    A[start scan()] --> B[find project + load config]
    B --> C[scan_sources]
    C --> D[select renderer]
    D --> E[render_all]
    E --> F[parse_all]
    F --> G[build_graph]
    G --> H[compute complexity]
    H --> I[run adapters]
    I --> J[engine.run_with_skips]
    J --> K[merge + sort findings]
    K --> L[apply waivers/baseline]
    L --> M[compute coverage]
    M --> N[build model summaries]
    N --> O[build render stats + skip report]
    O --> P[construct ScanResult]
```

## 3. Renderer Selection Details

Renderer selection lives in `_select_renderer`:
- If mode is MOCK: always use `JinjaRenderer`.
- If mode is COMPILED: always use `CompiledRenderer`.
- If mode is AUTO:
1. call `CompiledRenderer.is_available(...)`.
2. use COMPILED when compiled hit ratio >= `compiled_min_coverage`.
3. else fallback to MOCK.

Resulting per-node render mode is later collapsed into a dominant mode for rule dispatch (`_dominant_render_mode`).

## 4. Skip Tracking Model

Rule engine emits per-check skip events. Orchestrator aggregates through `_build_skip_report`:
- summary always computed.
- aggregated list always computed.
- per-pair list included based on effective skip-detail config.

Effective coverage for checks:

$$
\text{effective\_coverage\_pct} = 100 \times \left(1 - \frac{\text{total\_skips}}{\text{attempted\_checks}}\right)
$$

clamped to $[0, 100]$.

## 5. Model Summary Construction

`_build_model_summaries` joins four streams keyed by node id:
- parsed node status (`parse_success`, `render_uncertain`)
- coverage per-node tuples (`test`, `doc`)
- findings grouped into tier sets and waived count
- test results grouped by kind and execution
- check skip counts per node

Rows are sorted by `(score, name)` ascending for worst-first triage.

### 5.1 Scoring formula

Current score logic in orchestrator:
- Base: 100
- If no tests: `-25`
- Docs penalty: `-round((1 - doc_ratio) * 15)`
- Tier-1 rules: `-10 * count`, capped at `-40`
- Tier-2 rules: `-3 * count`, capped at `-20`
- Unexecuted tests: `-5 * count`, capped at `-15`
- Parse failed: `-10`
- Else if render uncertain: `-5`
- Skip penalty: up to `-5`
- Final clamp: `max(0, score)`

### 5.2 Output fields consumed by `dbtcov models`

`dbtcov models` uses these columns:
- Score
- Model
- Tests (declared data tests)
- Unit (unit test presence)
- Docs
- Parse
- Skips
- Findings
- File

It supports:
- `--results` (path to findings.json)
- `--min-score` (filter)
- `--sort score|name|tier`
- `--format console|json`

## 6. CLI Command Behaviors

### 6.1 scan

Key implementation points:
- Can list adapters and exit (`--list-adapters`).
- Applies CLI overrides into config shape.
- Optional gate in scan path (`--fail-on tier-1|tier-2|never`).
- Emits selected formats through reporter registry.
- Calls `exit_on_fatal` after report emission.

Fatal exits:
- 2: no models discovered.
- 3: parse-failed ratio >= 90%.

### 6.2 gate

`gate` validates a saved `ScanResult` payload and evaluates with current gate config.
No re-scan performed.

### 6.3 baseline

- `baseline capture`: run scan and write baseline entries.
- `baseline diff`: run scan, load baseline, print added/removed fingerprint sets.

## 7. Reporting Pipeline

`emit_reports` resolves reporter classes by format key:
- console reporter gets gate config and show-suppressed/skip-detail flags.
- json/sarif reporters get independent skip-detail values.
- when `json` or `sarif` are selected, `coverage.json` is always written.

## 8. Data Integrity Guarantees

Enforced by pydantic validators:
- `CoverageMetric.total >= covered`.
- Exact ratio check for most dimensions (`ratio == covered/total`) except approximate dimensions.
- Relative `Finding.file_path` requirement.
- `Finding.end_line` cannot precede `line`.
- strict enums and typed fields for skip reasons, tiers, categories, and test kinds.

## 9. Canonical Usage Examples

```bash
# full scan and artifacts
dbtcov scan --path . --format console json sarif --out dbtcov-out

# model triage
dbtcov models --results dbtcov-out/findings.json --min-score 70 --sort score

# gate using saved result
dbtcov gate --results dbtcov-out/findings.json --path .

# baseline lifecycle
dbtcov baseline capture --path .
dbtcov baseline diff --path .
```

This low-level design tracks current implementation behavior and the serialized contract emitted for downstream tooling.
