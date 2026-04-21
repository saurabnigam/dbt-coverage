# SPEC-19 — SQL Cyclomatic Complexity

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-01 (core domain), SPEC-05 (Jinja renderer), SPEC-06 (SQL parser), SPEC-18 (analysis graph)
**Blocks:** SPEC-20 (rule Q003), SPEC-22 (test_weighted_cc)

---

## 1. Purpose

Define a deterministic **per-model Cyclomatic Complexity** metric computed over the sqlglot AST (post-render) plus the raw Jinja source (pre-render), expose it as a typed field on every `ParsedNode` and as an aggregate summary on `ScanResult`, and make it available to:

- `Q003_HIGH_COMPLEXITY` (SPEC-20) — per-model finding when CC exceeds a threshold
- `test_weighted_cc` coverage dimension (SPEC-22) — weighting test coverage by model complexity
- reporters — console "top N" panel, SARIF `runs[0].properties.complexity`

**Analogy:** Sonar/Radon compute cyclomatic complexity on imperative code; we adapt the McCabe definition to SQL + Jinja. The metric is a *proxy for testing burden and maintenance risk*, not a ground-truth graph invariant. It is explicitly a heuristic.

---

## 2. Non-goals

- Not a formal control-flow graph. SQL is declarative; we count *decision surface* (CASE arms, predicates, joins, unions, subqueries), not execution paths.
- Not "cognitive complexity" (Sonar's nested-weight variant). Deferred.
- Not DAG/graph complexity (fan-in, fan-out, depth). That lives on the `AnalysisGraph` (SPEC-18) and is reported separately.
- No macro expansion beyond what `JinjaRenderer` already performs (SPEC-05). Jinja decision points are counted on the **raw** source; macro-internal control flow does not inflate a caller's CC.

---

## 3. Module layout

```
src/dbt_coverage/
  core/
    complexity.py            # ComplexityMetrics Pydantic model (new)
  complexity/
    __init__.py              # public API: compute_complexity(parsed_node) -> ComplexityMetrics
    sql_complexity.py        # AST visitor (sqlglot)
    jinja_complexity.py      # raw-source regex scanner for {% if %} / {% for %} / {% elif %}
```

Why split: `core.complexity` is a dependency-free type (consumed by `core.models.ScanResult`); `complexity/` contains the algorithm and imports sqlglot + regex.

---

## 4. Data model

### 4.1 `ComplexityMetrics` (`core/complexity.py`)

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class ComplexityMetrics(BaseModel):
    """
    Per-model SQL+Jinja cyclomatic complexity. Stable, serializable.
    Attribution fields let reporters explain *why* CC is high.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    cc: int = Field(ge=1, description="McCabe CC; always >= 1 (base path)")

    # Attribution (sum + 1 == cc when complete)
    case_arms: int = Field(default=0, ge=0)         # +1 per CASE WHEN arm
    join_count: int = Field(default=0, ge=0)        # +1 per Join node
    boolean_ops: int = Field(default=0, ge=0)       # +1 per AND/OR in WHERE/HAVING/ON
    set_op_arms: int = Field(default=0, ge=0)       # +1 per UNION/INTERSECT/EXCEPT arm beyond first
    subqueries: int = Field(default=0, ge=0)        # +1 per correlated subquery
    iff_count: int = Field(default=0, ge=0)         # +1 per IF/IIF
    jinja_ifs: int = Field(default=0, ge=0)         # +1 per {% if %} and {% elif %}
    jinja_fors: int = Field(default=0, ge=0)        # +1 per {% for %}

    # Provenance
    parsed_from_ast: bool = True                    # False if CC was computed from source only (fallback)
    uncertain: bool = False                         # True if node.render_uncertain or parse failed
```

### 4.2 Extensions to existing types

- `ScanResult` (SPEC-01 §4.4) gains:
  ```python
  complexity: dict[str, ComplexityMetrics] = Field(default_factory=dict)
  # keyed by node_id; only present when ScanResult has parsed nodes
  ```
- `CoverageMetric.dimension` Literal (SPEC-01 §4.3) gains `"complexity"`. The complexity coverage dimension is defined in §7 of this spec.

No changes to `ParsedNode` itself — complexity is computed on demand and attached to `ScanResult`. Rules access it via `RuleContext.complexity[node_id]` (SPEC-07 extension, see §6.3).

---

## 5. Algorithm

McCabe's definition: `CC = E - N + 2P` for a flow graph. For declarative SQL we redefine as **one point per decision**, starting from 1:

```
CC(model) = 1
          + count(CASE WHEN arms)
          + count(Joins)                                 # each join, any kind
          + count(top-level AND/OR inside WHERE/HAVING/ON)
          + count(UNION/INTERSECT/EXCEPT arms beyond first)
          + count(correlated subqueries)
          + count(IF / IIF)
          + count({% if %} / {% elif %})                 # raw Jinja source
          + count({% for %})                             # raw Jinja source
```

### 5.1 AST visitor (`complexity/sql_complexity.py`)

```python
from __future__ import annotations

from sqlglot import exp

from dbt_coverage.core.complexity import ComplexityMetrics


def compute_sql_cc(ast: exp.Expression | None) -> dict[str, int]:
    """
    Walk the AST once and tally decision points.
    Returns an attribution dict; caller sums it with Jinja counts.
    """
    if ast is None:
        return _zero()

    case_arms = 0
    join_count = 0
    boolean_ops = 0
    set_op_arms = 0
    subqueries = 0
    iff_count = 0

    # Walk: sqlglot's .walk() yields each subexpression once.
    for node in ast.walk():
        if isinstance(node, exp.Case):
            # args["ifs"] is the list of WHEN clauses; ELSE is a separate arg.
            case_arms += len(node.args.get("ifs") or [])

        elif isinstance(node, exp.Join):
            join_count += 1
            # Count boolean operators inside this join's ON clause
            on = node.args.get("on")
            if on is not None:
                boolean_ops += _count_bool_ops(on)

        elif isinstance(node, (exp.Where, exp.Having)):
            cond = node.this
            if cond is not None:
                boolean_ops += _count_bool_ops(cond)

        elif isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            # Each set-op node connects exactly two children;
            # N-way UNION chains as a left-leaning tree, so one node per arm beyond the first.
            set_op_arms += 1

        elif isinstance(node, (exp.If, exp.Iff)):
            iff_count += 1

        elif isinstance(node, exp.Subquery):
            if _is_correlated(node):
                subqueries += 1

    return {
        "case_arms": case_arms,
        "join_count": join_count,
        "boolean_ops": boolean_ops,
        "set_op_arms": set_op_arms,
        "subqueries": subqueries,
        "iff_count": iff_count,
    }


def _count_bool_ops(node: exp.Expression) -> int:
    """Count And/Or operators inside a predicate tree."""
    n = 0
    for child in node.walk():
        if isinstance(child, (exp.And, exp.Or)):
            n += 1
    return n


def _is_correlated(sub: exp.Subquery) -> bool:
    """A subquery is correlated if it references a table alias defined strictly outside itself."""
    inner_tables = {t.alias_or_name for t in sub.find_all(exp.Table)}
    # Walk ancestor chain collecting aliases visible above this subquery
    outer: set[str] = set()
    ancestor = sub.parent
    while ancestor is not None:
        for t in ancestor.find_all(exp.Table):
            if t not in sub.find_all(exp.Table):
                outer.add(t.alias_or_name)
        ancestor = ancestor.parent
    # Correlated iff any Column inside sub refers to an outer alias
    for col in sub.find_all(exp.Column):
        tbl = col.table
        if tbl and tbl in outer and tbl not in inner_tables:
            return True
    return False


def _zero() -> dict[str, int]:
    return {
        "case_arms": 0, "join_count": 0, "boolean_ops": 0,
        "set_op_arms": 0, "subqueries": 0, "iff_count": 0,
    }
```

### 5.2 Jinja scanner (`complexity/jinja_complexity.py`)

Pre-render text is scanned with a conservative regex. We intentionally avoid a full Jinja parser — the goal is to count *decision points the author wrote*, not to simulate execution.

```python
from __future__ import annotations

import re

_IF_RE = re.compile(r"\{%-?\s*(?:if|elif)\b", re.IGNORECASE)
_FOR_RE = re.compile(r"\{%-?\s*for\b", re.IGNORECASE)


def compute_jinja_cc(source: str) -> dict[str, int]:
    if not source:
        return {"jinja_ifs": 0, "jinja_fors": 0}
    return {
        "jinja_ifs": len(_IF_RE.findall(source)),
        "jinja_fors": len(_FOR_RE.findall(source)),
    }
```

Limitations (documented in §8):
- Does not dive into `{% macro %}` definitions; callers' CC is not inflated.
- Does not recognise `{% set %}` as a branch (it isn't).
- A `{% if %}` inside a comment `{# ... #}` could be double-counted. Acceptable — comments with template tags inside are extremely rare and the metric is heuristic.

### 5.3 Public API (`complexity/__init__.py`)

```python
from __future__ import annotations

from dbt_coverage.core import ParsedNode
from dbt_coverage.core.complexity import ComplexityMetrics

from .jinja_complexity import compute_jinja_cc
from .sql_complexity import compute_sql_cc


def compute_complexity(node: ParsedNode) -> ComplexityMetrics:
    sql = compute_sql_cc(node.ast)
    jinja = compute_jinja_cc(node.source_sql)

    cc = 1 + sum(sql.values()) + sum(jinja.values())

    return ComplexityMetrics(
        cc=cc,
        case_arms=sql["case_arms"],
        join_count=sql["join_count"],
        boolean_ops=sql["boolean_ops"],
        set_op_arms=sql["set_op_arms"],
        subqueries=sql["subqueries"],
        iff_count=sql["iff_count"],
        jinja_ifs=jinja["jinja_ifs"],
        jinja_fors=jinja["jinja_fors"],
        parsed_from_ast=node.parse_success and node.ast is not None,
        uncertain=node.render_uncertain or not node.parse_success,
    )


def compute_all(parsed_nodes: dict[str, ParsedNode]) -> dict[str, ComplexityMetrics]:
    return {nid: compute_complexity(n) for nid, n in parsed_nodes.items()}


__all__ = ["compute_complexity", "compute_all", "ComplexityMetrics"]
```

---

## 6. Integration points

### 6.1 Orchestrator (`cli/orchestrator.py`)

After parsing is complete, before rule-engine invocation:

```python
from dbt_coverage.complexity import compute_all as compute_complexity_all

complexity = compute_complexity_all(parsed_nodes)
# attached to ScanResult at the end:
result = ScanResult(..., complexity=complexity)
```

Engine receives `complexity` via a new `RuleContext.complexity` field (SPEC-07 extension).

### 6.2 Coverage aggregator

A new dimension `complexity` is added in SPEC-22, but computed here from the map. See §7 of this spec for the contract.

### 6.3 Rule engine (SPEC-07 extension)

`RuleContext` gains:

```python
complexity: dict[str, ComplexityMetrics] = field(default_factory=dict)
```

Engine threads the project-wide map into every rule context. Q003 (SPEC-20) and future complexity-sensitive rules read `ctx.complexity[ctx.node_id]`.

---

## 7. Complexity as a coverage dimension

To let the quality gate enforce a ceiling, complexity is exposed via the existing `CoverageMetric` shape:

```python
# dimension = "complexity"
# covered = count(models with cc <= threshold_warn)
# total   = count(all models with a ComplexityMetrics entry)
# ratio   = covered / total  (fraction of models under threshold)
# per_node[node_id] = (1 if cc <= threshold_warn else 0, 1)
```

This lets users write `coverage.complexity.min: 0.90` to enforce "≥90% of models under the complexity threshold", consistent with how other coverage dimensions work.

The threshold used here is `complexity.threshold_warn` from config (SPEC-08 §... — defined in SPEC-20 alongside the rule). Default: **15**.

---

## 8. Failure modes

| Case | Behavior |
|---|---|
| `node.ast is None` (parse failed) | Compute CC from Jinja only. `uncertain=True`, `parsed_from_ast=False`. |
| `node.render_uncertain=True` | Still compute. Flag `uncertain=True`; reporters display with `?` suffix. |
| sqlglot raises during walk | Treat as `ast=None`; emit a warning log; return a minimal `ComplexityMetrics(cc=1, uncertain=True)`. Never raise. |
| Empty source file | `ComplexityMetrics(cc=1)` (base path only). |
| Extreme CC (>500) | Still computed — used for diagnostics. Reporters clamp display to `"500+"` in tables for readability, full value retained in JSON. |
| Multi-statement file (rare in dbt) | sqlglot parses into `exp.Semicolon` chain; we walk the first top-level statement only (`ast.expressions[0]`) to avoid double-counting. Fallback: walk whole ast. |
| Macro call expanding to many joins at render time | Those joins appear in post-render AST → CC reflects reality. Intentional; the author did call a macro with complexity. |

---

## 9. Test plan (`tests/unit/complexity/`)

### 9.1 `test_sql_complexity.py`
- Minimal `SELECT 1` → cc = 1, all attribution zero.
- Single `CASE WHEN a=1 THEN 2 WHEN b=2 THEN 3 ELSE 4 END` → `case_arms=2`, cc=3.
- Two-way INNER JOIN with one AND predicate → `join_count=1`, `boolean_ops=1`, cc=3.
- `UNION ALL` of 3 selects → `set_op_arms=2`, cc=3.
- `IFF(x > 0, 1, 0)` → `iff_count=1`, cc=2.
- Correlated subquery `SELECT a FROM t WHERE x = (SELECT max(x) FROM t2 WHERE t2.k = t.k)` → `subqueries=1`, plus bool_ops for WHERE.
- Non-correlated subquery → `subqueries=0`.
- `None` AST → returns all-zero attribution.

### 9.2 `test_jinja_complexity.py`
- `"{% if foo %}{% endif %}"` → `jinja_ifs=1`.
- `"{% if a %}{% elif b %}{% endif %}"` → `jinja_ifs=2`.
- `"{% for x in xs %}{% endfor %}"` → `jinja_fors=1`.
- `"{%- if x -%}"` (whitespace-trim) → `jinja_ifs=1`.
- `"-- not jinja {% if %}"` → still counted (regex is conservative and this is acceptable per §8).
- Empty string → `{jinja_ifs: 0, jinja_fors: 0}`.

### 9.3 `test_compute_complexity.py` (integration within the module)
- ParsedNode with both SQL and Jinja decision points → cc = 1 + sum of both.
- ParsedNode with `ast=None` and `parse_success=False` → Jinja-only cc; `parsed_from_ast=False`, `uncertain=True`.
- ParsedNode with `render_uncertain=True` → `uncertain=True`, cc still computed.
- sqlglot raises monkey-patched on walk → returns `cc=1, uncertain=True`, no exception propagates.

### 9.4 `test_complexity_metric_roundtrip.py`
- `ComplexityMetrics(...).model_dump_json()` → `model_validate_json` round-trips with equality.
- `ScanResult` with a non-empty `complexity` dict round-trips.

---

## 10. Acceptance criteria

- [ ] `src/dbt_coverage/core/complexity.py`, `src/dbt_coverage/complexity/{__init__,sql_complexity,jinja_complexity}.py` exist with signatures per §4 and §5.
- [ ] `from dbt_coverage.complexity import compute_complexity, compute_all, ComplexityMetrics` works.
- [ ] `ScanResult.complexity: dict[str, ComplexityMetrics]` is serialized in JSON.
- [ ] All tests in §9 pass with ≥95% line coverage on the new module.
- [ ] No exception escapes `compute_complexity` on malformed input.
- [ ] `ruff check` + `mypy` clean on the new module.
- [ ] Running on the sample dbt project (tests/fixtures/sample_dbt_project) produces a `complexity` map of 4+ entries with `cc` ≥ 1 for every model.

---

## 11. Open questions

- Should `iff_count` include `COALESCE` / `NULLIF`? *Decision: no — they are value-selection, not branches. Revisit if users complain.*
- Should correlated-subquery detection traverse CTE boundaries? *Decision: yes — CTEs are syntactic sugar; `_is_correlated` already walks the full ancestor chain.*
