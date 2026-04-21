# SPEC-20 — Rule Q003: High Cyclomatic Complexity

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-01, SPEC-07 (rule engine), SPEC-19 (ComplexityMetrics)
**Blocks:** none

---

## 1. Purpose

Emit one `Finding` per model whose cyclomatic complexity exceeds a configurable warn threshold, and a second, more severe finding when it exceeds a block threshold. Complements SPEC-19 (which computes CC as a metric) by turning that metric into an actionable, gate-enforceable rule — directly analogous to Sonar's `CognitiveComplexity` / Radon's CC rules for Python.

---

## 2. Non-goals

- Does not compute CC — reads it from `RuleContext.complexity` populated by the orchestrator (SPEC-19).
- Does not prescribe refactor steps. Message surfaces top contributors (CASE arms, joins, unions) so the author can prioritise.
- Does not look at `AnalysisGraph` complexity (fan-in, fan-out). That's a separate rule family, out of scope.

---

## 3. Rule metadata

| Field | Value |
|---|---|
| `id` | `Q003` |
| `category` | `Category.QUALITY` |
| `finding_type` | `FindingType.CODE_SMELL` |
| `default_severity` (warn) | `Severity.MAJOR` |
| `default_severity` (block) | `Severity.CRITICAL` |
| `default_tier` (warn) | `Tier.TIER_2_WARN` |
| `default_tier` (block) | `Tier.TIER_1_ENFORCED` |
| `applies_to_node` | `True` |
| `requires_ast` | `False` (uses precomputed map) |
| `confidence_base` | `0.95` (metric is deterministic; the *threshold* is the opinion) |

---

## 4. Params schema

Configured under `rules.Q003` in `dbtcov.yml`. Pydantic model (lives in SPEC-08/config, but defined here for clarity):

```python
class Q003Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_warn: int = Field(default=15, ge=2)
    threshold_block: int = Field(default=30, ge=2)
    include_jinja: bool = True         # count {% if %} / {% for %} toward threshold
    exempt_models: list[str] = []      # glob patterns matched against ParsedNode.node_id or file_path

    @model_validator(mode="after")
    def _check_order(self) -> "Q003Params":
        if self.threshold_block < self.threshold_warn:
            raise ValueError("threshold_block must be >= threshold_warn")
        return self
```

Defaults chosen to match common industry defaults for complex SQL (Sonar uses 15 for SQL `high_complexity`; 30 is roughly "this is unmaintainable").

**`include_jinja=False` escape hatch:** for projects that use heavy Jinja templating deliberately, user can exclude Jinja branches from the threshold decision. The AST-only CC is recomputed as `cc - jinja_ifs - jinja_fors` at check time.

---

## 5. Detection logic

```python
from dbt_coverage.analyzers.rule_base import BaseRule
from dbt_coverage.core import Severity, Category, FindingType, Tier


class Q003HighComplexityRule(BaseRule):
    id = "Q003"
    default_severity = Severity.MAJOR
    default_tier = Tier.TIER_2_WARN
    category = Category.QUALITY
    finding_type = FindingType.CODE_SMELL
    description = "SQL model cyclomatic complexity exceeds threshold"
    confidence_base = 0.95
    applies_to_node = True
    requires_ast = False

    def check(self, ctx):
        if ctx.node.node_id is None:
            return
        metrics = ctx.complexity.get(ctx.node.node_id)
        if metrics is None:
            return

        params = self.params_for(ctx)  # Q003Params instance
        cc = metrics.cc
        if not params.include_jinja:
            cc = cc - metrics.jinja_ifs - metrics.jinja_fors

        if self._is_exempt(ctx, params.exempt_models):
            return

        if cc >= params.threshold_block:
            yield self._make(ctx, metrics, cc, params.threshold_block,
                             severity=Severity.CRITICAL,
                             tier=Tier.TIER_1_ENFORCED,
                             level="block")
        elif cc >= params.threshold_warn:
            yield self._make(ctx, metrics, cc, params.threshold_warn,
                             severity=Severity.MAJOR,
                             tier=Tier.TIER_2_WARN,
                             level="warn")

    def _make(self, ctx, metrics, cc, threshold, severity, tier, level):
        top = self._top_contributors(metrics, k=3)
        msg = (
            f"Cyclomatic complexity {cc} >= {level} threshold {threshold}. "
            f"Top contributors: {top}."
        )
        return self.make_finding(
            ctx,
            line=1, column=1,                   # file-level finding
            message=msg,
            code_context=f"Q003:{cc}",          # fingerprint-stable across cosmetic edits
            severity=severity,
            tier=tier,
            confidence=0.95,
        )

    @staticmethod
    def _top_contributors(metrics, k=3) -> str:
        parts = {
            "joins": metrics.join_count,
            "CASE arms": metrics.case_arms,
            "AND/OR": metrics.boolean_ops,
            "UNION arms": metrics.set_op_arms,
            "IF/IIF": metrics.iff_count,
            "correlated subqueries": metrics.subqueries,
            "{% if %}": metrics.jinja_ifs,
            "{% for %}": metrics.jinja_fors,
        }
        ordered = sorted(parts.items(), key=lambda kv: kv[1], reverse=True)
        return ", ".join(f"{name}={n}" for name, n in ordered[:k] if n > 0) or "none"

    @staticmethod
    def _is_exempt(ctx, patterns) -> bool:
        from fnmatch import fnmatch
        s = ctx.node.node_id or str(ctx.node.file_path)
        return any(fnmatch(s, p) for p in patterns)
```

---

## 6. Message format (stable)

```
Cyclomatic complexity {cc} >= {level} threshold {threshold}. Top contributors: joins=N, CASE arms=M, AND/OR=K.
```

- `{level}` ∈ `{"warn", "block"}`
- `{cc}` is the effective CC after applying `include_jinja`
- Top contributors limited to 3 non-zero fields (deterministic, sorted by count desc, tie-broken by attribution order above)

Baseline-safe: message variance doesn't affect fingerprint because fingerprint uses `code_context="Q003:{cc}"`. If only `cc` changes (even by 1), fingerprint changes — this is intentional: a complexity shift is an actual behavioural change, not cosmetic.

---

## 7. Fingerprinting

```python
compute_fingerprint(
    rule_id="Q003",
    file_path=ctx.node.file_path,
    code_context=f"Q003:{cc}",
)
```

Trade-off: refactoring that reduces CC by 1 creates a new finding and retires the old one. We accept this because the baseline-new-only gate would not then hide a regression.

---

## 8. Interaction with SPEC-19

- This rule **never** computes CC itself. If `ctx.complexity.get(node_id)` is missing or `uncertain=True` with `parsed_from_ast=False`, the rule skips that model silently (no false alarm from broken parses).
- If `metrics.uncertain=True` but `parsed_from_ast=True`, the rule still fires — CC is meaningful, just with a caveat already surfaced on the metric itself.

---

## 9. Interaction with the gate

- Warn findings (Tier-2) count only when `cfg.gate.fail_on_tier == TIER_2_WARN`.
- Block findings (Tier-1) always count.
- Because complexity is *also* a coverage dimension (SPEC-19 §7), users get two independent knobs:
  - `rules.Q003.threshold_block`: per-model absolute cap.
  - `coverage.complexity.min`: project-wide "at least X% of models under threshold_warn".

Typical combinations:
- Strict: `threshold_block=30`, `coverage.complexity.min=0.90`.
- Legacy-onboarding: `threshold_block=60`, `coverage.complexity.min=0.50`, rely on baseline-new-only for regressions.

---

## 10. Failure modes

| Case | Behavior |
|---|---|
| `ctx.complexity` empty (no complexity computed) | Rule emits zero findings silently. Not an error — lets the rule engine run even when orchestrator skips complexity. |
| `ctx.node.node_id is None` (loose SQL file, not a dbt model) | Skip. Q003 is a model-level rule. |
| `metrics.cc == threshold_warn` | Fires at warn level (inclusive). |
| `metrics.cc == threshold_block` | Fires at block level (inclusive, overrides warn). |
| Both thresholds equal | Valid config; behaves as block-only. |
| `threshold_block < threshold_warn` | Config validation rejects. |
| Model in `exempt_models` glob | Skipped; no finding. |
| `include_jinja=False` and all CC comes from Jinja | Effective CC may drop below warn threshold → no finding. |

---

## 11. Tests (`tests/unit/analyzers/packs/quality/test_q003.py`)

- Model with `cc=10`, thresholds 15/30 → 0 findings.
- Model with `cc=15`, thresholds 15/30 → 1 finding, severity MAJOR, tier TIER_2_WARN.
- Model with `cc=30`, thresholds 15/30 → 1 finding, severity CRITICAL, tier TIER_1_ENFORCED (no duplicate warn).
- Model with `cc=45` but exempt_models matches node_id → 0 findings.
- Model with `cc=20` (jinja_ifs=10) and `include_jinja=False` → effective cc=10 → 0 findings.
- Model with `node_id=None` → 0 findings.
- Model not in `ctx.complexity` → 0 findings.
- Metrics with `uncertain=True, parsed_from_ast=False` → 0 findings.
- Metrics with `uncertain=True, parsed_from_ast=True` → 1 finding (still fires).
- Message format: contains `"complexity {cc}"`, `">= {level} threshold {threshold}"`, and up to 3 top contributors.
- Fingerprint stable when message cosmetically changes but cc constant.
- Config validation: `threshold_block=10, threshold_warn=20` → ValueError.

---

## 12. Acceptance criteria

- [ ] `src/dbt_coverage/analyzers/packs/quality/q003_high_complexity.py` exists.
- [ ] Registered in `analyzers/rule_registry.py::_builtin_rule_classes`.
- [ ] `Q003Params` discoverable by the config loader; unknown rule params rejected.
- [ ] All tests in §11 pass; ≥95% line coverage on the rule module.
- [ ] `ruff check` + `mypy --strict` clean.
- [ ] Running on the sample project (after SPEC-19 populates complexity) yields a Q003 finding on the high-CC fixture model and zero on simple ones.

---

## 13. Open questions

- Should Q003 support per-severity overrides (`severity_map: {warn: MINOR, block: BLOCKER}`)? *Proposal: defer until a second rule needs the same facility. A uniform `SeverityMap` lives in SPEC-08's generic rule config, which applies here automatically via `ctx.severity_for(rule_id, level)`.*
- Should `threshold_*` support per-directory overrides (`marts/`: 20, `staging/`: 10)? *Proposal: accept in phase 2 via the same `exempt_models` glob mechanism extended to `overrides: [{pattern, threshold_warn, threshold_block}]`. Out of scope for v1.*
