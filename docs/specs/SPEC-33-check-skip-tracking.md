# SPEC-33 — Check-skip tracking

## 1. Problem

The engine in [src/dbt_coverage/analyzers/rule_engine.py](../../src/dbt_coverage/analyzers/rule_engine.py) silently skips any AST rule on nodes with `parse_success=False` or `render_uncertain=True` — about 57% of models on pontus-models. Rule exceptions are also swallowed to the log. The report currently cannot answer *"which checks ran, which didn't, and why?"*

## 2. Goals

- Surface every silently-skipped `(rule, node)` pair with a typed reason.
- Support tiered detail (`summary` / `aggregated` / `per_pair`) via config, per reporter.
- CI-first: `RULE_ERROR` and `ADAPTER_FAILED` default to gate-blocking; parse-related skips default to reporting-only until COMPILED mode closes the gap.

## 3. Types

```python
class CheckSkipReason(StrEnum):
    PARSE_FAILED      = "parse_failed"
    RENDER_UNCERTAIN  = "render_uncertain"
    RULE_DISABLED     = "rule_disabled"
    RULE_SCOPED_OUT   = "rule_scoped_out"
    ADAPTER_MISSING   = "adapter_missing"
    ADAPTER_FAILED    = "adapter_failed"
    MODE_REQUIRED     = "mode_required"
    RULE_ERROR        = "rule_error"

class CheckSkip(BaseModel):
    rule_id: str
    node_id: str | None
    reason: CheckSkipReason
    details: str | None = None

class AggregatedCheckSkip(BaseModel):
    rule_id: str
    reason: CheckSkipReason
    count: int
    affected_node_ids: list[str]
    sample_details: str | None

class CheckSkipSummary(BaseModel):
    total_skips: int
    attempted_checks: int
    effective_coverage_pct: float
    by_reason: dict[CheckSkipReason, int]
    by_rule: dict[str, int]
    affected_nodes: int

class ScanResult(BaseModel):
    # existing fields...
    check_skip_summary: CheckSkipSummary = ...
    check_skips_aggregated: list[AggregatedCheckSkip] = []
    check_skips: list[CheckSkip] = []
```

`check_skip_summary` is always populated; deeper arrays are opt-in via config.

## 4. Engine contract

```python
def run(self, parsed_nodes) -> EngineResult:  # (findings, skips, attempted)
    for rule in rules:
        if not rule.enabled: record(RULE_DISABLED); continue
        if rule.required_render_mode and ctx.render_mode != required: record(MODE_REQUIRED); continue
        if rule.required_adapter not in adapter_results: record(ADAPTER_MISSING); continue
        if adapter_results[rule.required_adapter].failed: record(ADAPTER_FAILED); continue
        for node in nodes:
            attempted += 1
            if not rule.scope_matches(node): record(RULE_SCOPED_OUT); continue
            if rule.requires_ast and not node.parse_success: record(PARSE_FAILED); continue
            if rule.requires_ast and node.render_uncertain: record(RENDER_UNCERTAIN); continue
            try: findings.extend(rule.check(ctx))
            except Exception as exc: record(RULE_ERROR, details=repr(exc))
```

`BaseRule` gains `required_render_mode: RenderMode | None = None` and `required_adapter: str | None = None`.

## 5. Config

```yaml
reports:
  skip_detail: aggregated            # summary | aggregated | per_pair
  console:
    skip_detail: summary
  json:
    skip_detail: aggregated
  sarif:
    skip_detail: per_pair

gate:
  thresholds:
    skips:
      parse_failed_max: null
      render_uncertain_max: null
      rule_error_max: 0
      adapter_failed_max: 0
      total_max: null
```

CLI: `--skip-detail {summary,aggregated,per_pair}` on `scan`.

Resolution order per reporter: per-reporter config > global `reports.skip_detail` > CLI flag > default.

## 6. Reporters

- **Console**: new "Skipped checks" section between findings and coverage; banner when `RULE_ERROR > 0`.
- **JSON**: always emits `check_skip_summary`; conditionally emits `check_skips_aggregated` / `check_skips`.
- **SARIF**: `runs[0].invocations[0].toolExecutionNotifications[]` per 2.1.0 §3.20.21. `level: "error"` for `RULE_ERROR`/`ADAPTER_FAILED`, `"warning"` otherwise.
- **`dbtcov models`**: new `Skips` column.

## 7. Gate

New threshold section; any `null` threshold is skipped; numeric thresholds fire a `SKIPS_ABOVE_MAX` `GateReason` when exceeded.

## 8. Tests

- Unit tests for every skip reason (engine pre-dispatch ladder).
- `effective_coverage_pct` math edge cases (0 attempted, 100% skipped).
- Per-reporter resolution order.
- Gate threshold firing on `RULE_ERROR`.
- Integration: fixture with a throwing test rule + a model with `parse_success=False` → assert SARIF notifications, JSON skips, console summary.
