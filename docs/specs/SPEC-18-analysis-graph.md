# SPEC-18 — Analysis Graph (minimal)

**Status:** draft
**Depends on:** SPEC-01, SPEC-03, SPEC-06
**Blocks:** SPEC-07, SPEC-16a, SPEC-17a

---

## 1. Purpose

Precomputed, cached, O(1)-lookup data structure sitting between parser and rule engine. Rules query it; they never re-walk ASTs or the project index themselves.

**Phase-1 minimal scope:**
- **DAG** — ref→refd edges from every `ParsedNode.refs`.
- **Canonical AST cache** — `sqlglot.optimizer.optimize(ast)` per node, for duplicate detection (SPEC-16a).
- **Declared-vs-actual columns** — YAML `columns:` list vs. AST SELECT projections.

**Phase-2 extension (SPEC-15):** full column-level lineage.

---

## 2. Non-goals (phase-1)

- No column-level lineage (deferred).
- No cross-project graph merging.
- No graph serialization — rebuilt per scan (cached per-node by SPEC-19 later).

---

## 3. Module layout

```
src/dbt_coverage/graph/
  __init__.py
  analysis_graph.py         # AnalysisGraph class + build() entrypoint
  dag.py                    # simple DAG builder
  canonical.py              # sqlglot.optimizer.optimize wrapper + caching
  columns.py                # declared_vs_actual implementation
```

---

## 4. API Surface

### 4.1 `analysis_graph.py`

```python
from sqlglot.expressions import Expression
from dbt_coverage.core import ParsedNode, ColumnDiff
from dbt_coverage.scanners import ProjectIndex

class AnalysisGraph:
    def __init__(
        self,
        project_index: ProjectIndex,
        parsed_nodes: dict[str, ParsedNode],  # keyed by node_id
    ):
        """Internally builds DAG + canonical ASTs lazily on first query."""

    # --- DAG ---
    def get_upstream(self, node_id: str) -> set[str]:
        """Direct upstream node_ids (one hop)."""

    def get_downstream(self, node_id: str) -> set[str]:
        """Direct downstream node_ids (one hop)."""

    def get_transitive_downstream(self, node_id: str) -> set[str]:
        """All reachable downstream node_ids."""

    def is_leaf(self, node_id: str) -> bool:
        """No downstream refs AND no exposures reference it."""

    # --- Canonical AST ---
    def canonical_ast(self, node_id: str) -> Expression | None:
        """
        Returns sqlglot.optimizer.optimize(ast) for the node, cached.
        Returns None if parse_success=False or optimizer fails.
        """

    def similarity(self, a_id: str, b_id: str) -> float:
        """
        0..1 similarity between two nodes' canonical ASTs via sqlglot.diff.
        Formula: 1 - (num_edits / max(len(a_nodes), len(b_nodes))).
        Returns 0.0 if either canonical_ast is None.
        """

    # --- Columns ---
    def declared_vs_actual_columns(self, node_id: str) -> ColumnDiff | None:
        """
        Returns ColumnDiff(declared_only, actual_only, matching).
        Returns None when:
          - ast is None
          - AST contains SELECT * and no catalog is available to expand
          - yml_meta is None
        """

    # --- Lineage (phase 2 extension point; returns UNKNOWN for now) ---
    def is_column_used_downstream(self, node_id: str, col: str) -> bool | None:
        """Phase-1 stub: always returns None (UNKNOWN). Full impl in SPEC-15."""

def build(
    project_index: ProjectIndex,
    parsed_nodes: dict[str, ParsedNode],
) -> AnalysisGraph:
    """Factory that constructs and eagerly validates the DAG."""
```

### 4.2 `dag.py`

```python
class DAG:
    """
    Directed graph, node_ids as vertices.
    Stored as adjacency-list dicts:
      _children: dict[str, set[str]]  # node_id -> set of children
      _parents: dict[str, set[str]]   # node_id -> set of parents
    """
    def add_edge(self, parent: str, child: str) -> None: ...
    def children(self, node_id: str) -> set[str]: ...
    def parents(self, node_id: str) -> set[str]: ...
    def descendants(self, node_id: str) -> set[str]: ...
    def detect_cycles(self) -> list[list[str]]: ...
```

**DAG construction:**
- Iterate every `ParsedNode.refs` (list of `__REF_name__` strings).
- Resolve each `__REF_name__` → target `node_id` via `project_index.models` (strip `__REF_` prefix + `__` suffix, match model name).
- If a ref can't be resolved (e.g. typo) → stored as orphan; rule Q007 later flags it.
- Cycles are rare in dbt but possible; detect and report in `scan_errors` (don't crash).

### 4.3 `canonical.py`

```python
from sqlglot.expressions import Expression
from sqlglot.optimizer import optimize

def canonicalize(ast: Expression, dialect: str) -> Expression | None:
    """
    Applies sqlglot optimizer rules: qualify_columns, normalize, eliminate_subqueries.
    Returns None on optimizer failure (rare; caught broadly).
    """
```

**Why these rules:**
- `qualify_columns` — adds explicit table aliases (`a.x` → `orders.x`) so diff isn't fooled by aliasing differences.
- `normalize` — NNF-form for boolean expressions, consistent clause ordering.
- `eliminate_subqueries` — flattens trivially-nested selects.

**Not applied:** `pushdown_predicates`, `simplify` — too aggressive; could merge structurally different but semantically similar queries, hurting duplicate detection precision.

### 4.4 `columns.py`

```python
from sqlglot.expressions import Expression, Star
from dbt_coverage.core import ColumnDiff
from dbt_coverage.scanners import YamlModelMeta

def extract_select_columns(ast: Expression) -> list[str] | None:
    """
    Walks top-level SELECT, returns projection column names.
    Returns None if SELECT * or non-SELECT top-level.
    """

def declared_vs_actual(
    yml_meta: YamlModelMeta | None,
    ast: Expression | None,
) -> ColumnDiff | None:
    """
    Compares yml_meta.columns (names) vs. AST projection names.
    Returns None if any input is None/unknown.
    """
```

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| Node with `parse_success=False` | `canonical_ast` returns None; excluded from DAG edges from this node (still has incoming edges if others ref it) |
| Ref to non-existent model | Kept as orphan in DAG; `get_downstream` of target id simply empty (it doesn't exist) |
| Circular dbt ref (illegal but possible) | Logged to `scan_errors`; DAG allows cycle; `descendants` handles cycles via visited-set |
| Node with `SELECT *` | `declared_vs_actual` returns None (can't compare) |
| Node has YAML but no SQL match | Not in `parsed_nodes` dict; no DAG node for it |
| 1000+ models | Construction O(V+E), queries O(1); acceptable |
| Similarity of identical ASTs | Returns 1.0 |
| Similarity of two None asts | Returns 0.0 |
| `yml_meta` missing | `declared_vs_actual` returns None |
| AST with CTE-only (no main SELECT) | `extract_select_columns` returns the final SELECT's columns; if truly no final SELECT, returns None |

---

## 6. Test plan (`tests/unit/graph/`)

### 6.1 `test_dag.py`
- 3-node linear chain (A→B→C) → correct upstream/downstream/descendants.
- Diamond (A→B, A→C, B→D, C→D) → `descendants(A) == {B,C,D}`.
- Self-cycle (A→A) → detected in `detect_cycles`, queries still terminate.
- Orphan node → empty parents/children sets.

### 6.2 `test_canonical.py`
- Two equivalent queries (`SELECT a.x FROM t a` vs. `SELECT x FROM t`) canonicalize to equal trees (post qualify_columns they match).
- Non-equivalent queries canonicalize to different trees.
- Optimizer failure (pathological AST) returns None, no crash.

### 6.3 `test_columns.py`
- Plain projection `SELECT a, b, c FROM t` → `["a","b","c"]`.
- With aliases `SELECT x AS renamed FROM t` → `["renamed"]`.
- `SELECT * FROM t` → None.
- `declared_vs_actual` with YAML `[a, b]` and AST `[a, c]` → `declared_only=[b]`, `actual_only=[c]`, `matching=[a]`.

### 6.4 `test_analysis_graph.py`
- Build graph for 5-model fixture, verify DAG edges match expected.
- `similarity(same_node, same_node)` == 1.0.
- `is_column_used_downstream(...)` always returns None in phase-1 (stub).

**Coverage target:** 90%+.

---

## 7. Acceptance criteria

- [ ] `AnalysisGraph.build()` on `examples/basic_project/` completes in <500ms
- [ ] `get_downstream` returns correct set for a known model in fixture
- [ ] `canonical_ast` returns non-None for every successfully-parsed node
- [ ] `similarity(nodeA, nodeA) == 1.0`; `similarity(nodeA, nodeB_90pct_copy) ≥ 0.85` (calibration check for SPEC-16a)
- [ ] `ruff`, `mypy --strict` clean
- [ ] `pytest tests/unit/graph/` ≥90% coverage
- [ ] Lazy-build: constructing `AnalysisGraph` without querying doesn't call `sqlglot.optimizer.optimize` (verified by mock + call count)

---

## 8. Open questions

- Should canonical ASTs be computed in parallel? **Proposal:** no in phase 1 — sqlglot optimizer is fast (<10ms/model), parallelism overhead not worth it until we see profiling data.
- Cache canonical ASTs on disk (keyed on source_hash)? **Proposal:** defer to SPEC-19 (unified cache); not this spec.
