# SPEC-07 — Rule Engine + Registry

**Status:** draft
**Depends on:** SPEC-01, SPEC-03, SPEC-05, SPEC-06, SPEC-18
**Blocks:** SPEC-08a, SPEC-16a, SPEC-17a

---

## 1. Purpose

Define the Rule protocol, the registry (load built-in rules + discover third-party rule packs via entry points), and the engine that:
- Runs each enabled rule across all applicable nodes.
- Applies config overrides (severity, tier, enabled, params) from `DbtcovConfig`.
- Deduplicates findings, computes fingerprints, filters below `confidence_threshold`.
- Collects `Finding` objects into a list for normalization.

---

## 2. Non-goals

- No rule implementations here — they live in `analyzers/packs/` (SPEC-08a, SPEC-17a) and `analyzers/duplicates.py` (SPEC-16a).
- No coverage calculation (SPEC-09a).
- No reporter formatting (SPEC-10a).
- No gate logic (SPEC-11).

---

## 3. Module layout

```
src/dbt_coverage/analyzers/
  __init__.py
  rule_engine.py             # Engine.run(), RuleContext
  rule_registry.py           # discovery + override application
  rule_base.py               # Rule Protocol + BaseRule helper class
  normalization.py           # fingerprint helpers, code-context extraction
```

---

## 4. API Surface

### 4.1 `rule_base.py`

```python
from typing import Protocol, Iterable, ClassVar
from dbt_coverage.core import (
    Finding, Severity, Category, FindingType, Tier, ParsedNode,
)

class RuleContext:
    """Passed to every Rule.check() call. Immutable view."""
    node: ParsedNode
    node_id: str | None
    graph: "AnalysisGraph"
    project: "ProjectIndex"
    artifacts: "Artifacts | None"   # phase-2
    params: dict                    # rule-specific params from config
    confidence_min: float           # effective confidence threshold for this rule

class Rule(Protocol):
    id: ClassVar[str]                      # "Q001"
    default_severity: ClassVar[Severity]
    default_tier: ClassVar[Tier]
    category: ClassVar[Category]
    finding_type: ClassVar[FindingType]
    description: ClassVar[str]
    confidence_base: ClassVar[float]       # 0..1
    applies_to_node: ClassVar[bool] = True # False for project-level rules (e.g. R001 duplicates)

    def check(self, ctx: RuleContext) -> Iterable[Finding]: ...

class BaseRule:
    """Helper base with make_finding() convenience."""
    def make_finding(
        self,
        ctx: RuleContext,
        line: int,
        column: int,
        message: str,
        *,
        end_line: int | None = None,
        end_column: int | None = None,
        code_context: str,                 # used for fingerprint
        confidence: float | None = None,   # defaults to cls.confidence_base
        severity_override: Severity | None = None,
        tier_override: Tier | None = None,
    ) -> Finding: ...
```

**Why `applies_to_node=False`:** rules like R001 (duplicate detection) need the full project, not a single node. The engine dispatches to `check_project(ctx_project)` instead of per-node — see §4.3.

### 4.2 `rule_registry.py`

```python
from importlib.metadata import entry_points
from dbt_coverage.core import Severity, Tier
from dbt_coverage.utils import DbtcovConfig

class RegisteredRule:
    rule_cls: type[Rule]
    enabled: bool
    effective_severity: Severity
    effective_tier: Tier
    effective_confidence_min: float
    params: dict

def discover_rules() -> list[type[Rule]]:
    """
    Loads:
      1. Built-in rules from dbt_coverage.analyzers.packs.* (hard-coded list in phase-1).
      2. Third-party rules via entry-point group `dbt_coverage.rules`.
    Returns all rule classes (enabled + disabled). Validates no duplicate `id` values.
    """

def apply_overrides(
    rule_classes: list[type[Rule]],
    config: DbtcovConfig,
) -> list[RegisteredRule]:
    """
    For each rule class, resolve effective severity/tier/confidence/params from:
      rule_cls defaults ← config.rules[rule_id] overrides
    Emit warning for unknown rule IDs in config (typos).
    """
```

**Entry-point group name:** `dbt_coverage.rules`. Plugins register like:
```toml
[project.entry-points."dbt_coverage.rules"]
my_rule = "my_pkg.rules:MyRule"
```

**Built-in rules registered in phase-1 MVP:**
- `dbt_coverage.analyzers.packs.quality.q001_select_star.Q001SelectStarRule`
- `dbt_coverage.analyzers.packs.quality.q002_missing_pk.Q002MissingPkRule`
- `dbt_coverage.analyzers.packs.performance.p001_cross_join.P001CrossJoinRule`
- `dbt_coverage.analyzers.duplicates.R001DuplicateModelsRule`

### 4.3 `rule_engine.py`

```python
class Engine:
    def __init__(
        self,
        registered_rules: list[RegisteredRule],
        graph: "AnalysisGraph",
        project: "ProjectIndex",
        artifacts: "Artifacts | None" = None,
        confidence_threshold: float = 0.7,
    ): ...

    def run(self, parsed_nodes: dict[str, ParsedNode]) -> list[Finding]:
        """
        For each enabled rule:
          - if applies_to_node: iterate parsed_nodes, call rule.check(ctx) per node.
          - else: call rule.check(ctx_project) once with project-wide context.
        Collects all findings. Post-processes:
          - dedup by fingerprint
          - drop findings with confidence < rule.effective_confidence_min OR < engine.confidence_threshold
          - apply severity/tier overrides from registration
        """
```

**Per-node execution loop (pseudocode):**
```python
for rr in self.rules:
    if not rr.enabled: continue
    rule = rr.rule_cls()
    if rule.applies_to_node:
        for nid, node in parsed_nodes.items():
            if node.render_uncertain and rule_needs_ast(rule):
                continue    # skip AST-dependent rules on uncertain nodes
            if not node.parse_success and rule_needs_ast(rule):
                continue
            ctx = RuleContext(node=node, node_id=nid, graph=graph, project=project,
                              artifacts=artifacts, params=rr.params,
                              confidence_min=rr.effective_confidence_min)
            try:
                for f in rule.check(ctx):
                    findings.append(self._postprocess(f, rr))
            except Exception as e:
                log_rule_crash(rule.id, nid, e)   # never let one rule kill the scan
    else:
        # project-level rule (R001 etc.)
        ctx = RuleContext(node=None, ...)
        try:
            for f in rule.check(ctx):
                findings.append(self._postprocess(f, rr))
        except Exception as e:
            log_rule_crash(rule.id, None, e)
return self._dedupe(findings)
```

**Crash isolation:** one rule throwing an uncaught exception doesn't crash the scan. Log to `ScanResult` metadata (added as `rule_crashes: list[str]` in a future spec; for phase-1 MVP, just structured log).

### 4.4 `normalization.py`

```python
def extract_code_context(node: ParsedNode, line: int, context_lines: int = 2) -> str:
    """
    Returns a normalized snippet around `line` (±context_lines), whitespace-collapsed.
    Used as `code_context` input to fingerprint.
    Normalization: strip leading whitespace, collapse runs of whitespace to single space,
    lowercase identifiers (to resist cosmetic reformatting).
    """
```

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| Config references unknown rule ID (typo) | Warning logged, ignored |
| Two rules register with same `id` | `discover_rules()` raises `ConfigError` (fail fast) |
| Rule raises during `check()` | Caught; scan continues; logged |
| Rule emits `Finding` with `line=0` | Pydantic validator rejects → caught → logged, finding dropped |
| Rule emits Finding with file_path pointing outside project | Pydantic validator (absolute path check) catches |
| Rule emits 100 Findings with same fingerprint | Deduped to 1 |
| Node `render_uncertain=True` | Skip rules that need AST; still run text-based rules (flag `rule_needs_ast` per rule class) |
| Project has zero models | Per-node rules yield nothing; project-level rules run with empty `parsed_nodes` |
| `confidence_base` is 0.0 on a rule | Rule findings are always suppressed (valid way to "soft-disable" a rule) |

---

## 6. Test plan (`tests/unit/analyzers/`)

### 6.1 `test_rule_registry.py`
- Built-in rules discovered (4 expected in phase-1 MVP).
- Duplicate rule ID across plugins → `ConfigError`.
- Unknown rule ID in config → warning, not error.
- Override applied: config sets `Q001` to `severity=MINOR, tier=TIER_2_WARN` → `RegisteredRule.effective_severity == MINOR`.

### 6.2 `test_rule_engine.py`
- Synthetic rule that emits 1 finding per node → engine aggregates correctly over 3 nodes.
- Synthetic rule that raises → engine logs, continues, other rules still run.
- Two rules emit identical finding (same fingerprint) → deduped.
- Confidence filtering: rule emits conf=0.5, threshold=0.7 → dropped.
- Project-level rule: `applies_to_node=False` → called once.
- Rule requiring AST receives a render_uncertain node → skipped (rule's check not invoked).

### 6.3 `test_normalization.py`
- `extract_code_context(node, line=5)` with 2 lines of context returns 5 lines (or fewer if near file edges).
- Different whitespace variants of the same SQL → identical normalized string.

**Coverage target:** 92%+ (engine dispatch has several branches).

---

## 7. Acceptance criteria

- [ ] `discover_rules()` returns exactly 4 rule classes in phase-1 MVP (Q001, Q002, P001, R001)
- [ ] Engine.run on a fixture project produces deterministic Finding list (same input → same output, ordered by `(file_path, line, rule_id)`)
- [ ] One rule crashing does not prevent others from running (verified via test with injected-crash rule)
- [ ] `ruff`, `mypy --strict` clean
- [ ] `pytest tests/unit/analyzers/test_rule_{engine,registry,base}.py` ≥92% coverage
- [ ] Entry-point discovery tested with a fixture package installed via `pip install -e tests/fixtures/test_plugin`

---

## 8. Open questions

- Should rule crashes become Findings themselves (BLOCKER severity, rule_id `INTERNAL`)? **Proposal:** yes, gives operators visibility via SARIF. Add to spec or defer — inclined to add now, small surface. **Decision:** add now. Engine emits a `Finding(rule_id="INTERNAL_CRASH", severity=BLOCKER, ...)` for each rule crash.
- Parallel rule execution across nodes? **Proposal:** defer — rules are fast; correctness-first for v1.
