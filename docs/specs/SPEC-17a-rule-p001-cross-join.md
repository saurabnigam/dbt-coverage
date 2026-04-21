# SPEC-17a — Rule P001: Cross-Join / Cartesian Product

**Status:** draft
**Depends on:** SPEC-01, SPEC-06, SPEC-07, SPEC-18
**Blocks:** none

---

## 1. Purpose

Detect unintended Cartesian products — joins without `ON`/`USING` clauses and without a matching `WHERE` predicate linking the two tables. These are almost always bugs (or intentional but costly) and are Tier-1 gate-blocking.

---

## 2. Detection logic

For every `sqlglot.expressions.Join` node in the AST:

1. **Explicit cross-join keyword.** If `join.kind` is `"CROSS"` → flag (unless exempted, see §3).
2. **Implicit cross-join via missing predicate.** If `join.args.get("on")` is None AND `join.args.get("using")` is None:
   - Walk the enclosing SELECT's `WHERE` clause looking for any `exp.EQ` where one side references the join's LHS table and the other side references the RHS table (via `exp.Column` with matching `table` attribute).
   - If no such predicate found → flag.

**Confidence:** 0.95 for explicit CROSS, 0.85 for implicit (inference-based; WHERE-clause walk may miss exotic connection predicates like `BETWEEN`/`IN` subqueries).

---

## 3. Exemptions

| Case | Exempt? | Rationale |
|---|---|---|
| Explicit `CROSS JOIN UNNEST(array)` (BigQuery/Snowflake flatten pattern) | Yes | Lateral-flatten idiom, not cartesian |
| `LATERAL` joins | Yes | Semantically scoped, not cartesian |
| RHS is a single-row subquery (`SELECT COUNT(*) FROM x`) | Yes (best-effort) | Single-row × N = N, no blow-up |
| User-annotated `-- dbtcov:ignore P001` on the JOIN line | Yes | Escape hatch for intentional cases |
| `FROM a, b WHERE a.x = b.y` (comma join) | **No** (still evaluated) | Comma joins are cross-joins; WHERE walk decides |

---

## 4. Module layout

```
src/dbt_coverage/analyzers/packs/performance/
  __init__.py
  p001_cross_join.py
```

---

## 5. Rule class

```python
from sqlglot import expressions as exp
from dbt_coverage.analyzers.rule_base import BaseRule
from dbt_coverage.core import Severity, Category, FindingType, Tier

class P001CrossJoinRule(BaseRule):
    id = "P001"
    default_severity = Severity.CRITICAL
    default_tier = Tier.TIER_1_ENFORCED
    category = Category.PERFORMANCE
    finding_type = FindingType.BUG
    description = "Cross-join / cartesian product without filter"
    confidence_base = 0.95
    applies_to_node = True
    requires_ast = True

    _IGNORE_PRAGMA = "dbtcov:ignore P001"

    def check(self, ctx):
        if ctx.node.ast is None:
            return
        for select in ctx.node.ast.find_all(exp.Select):
            for join in select.args.get("joins", []) or []:
                if self._is_exempt(join):
                    continue
                explicit_cross = (join.kind or "").upper() == "CROSS"
                has_on = join.args.get("on") is not None
                has_using = join.args.get("using") is not None
                if explicit_cross:
                    yield self._make(ctx, select, join, confidence=0.95,
                                     message="Explicit CROSS JOIN detected")
                elif not has_on and not has_using:
                    if not self._has_connecting_where(select, join):
                        yield self._make(ctx, select, join, confidence=0.85,
                                         message="JOIN without ON/USING and no connecting WHERE predicate")

    def _is_exempt(self, join: exp.Join) -> bool:
        # CROSS JOIN UNNEST / LATERAL
        if join.args.get("lateral"):
            return True
        rhs = join.this
        if isinstance(rhs, exp.Unnest): return True
        # Pragma check: look at sql text near this node
        sql = join.sql()
        if self._IGNORE_PRAGMA in sql: return True
        # Single-row subquery heuristic
        if isinstance(rhs, exp.Subquery):
            inner = rhs.this
            if isinstance(inner, exp.Select):
                # COUNT(*) / MAX(*) / sole-aggregate projection with no GROUP BY
                has_group = inner.args.get("group") is not None
                all_agg = all(isinstance(e, exp.AggFunc) for e in inner.expressions)
                if not has_group and all_agg: return True
        return False

    def _has_connecting_where(self, select: exp.Select, join: exp.Join) -> bool:
        where = select.args.get("where")
        if where is None:
            return False
        lhs_tables = self._extract_tables_before(select, join)
        rhs_tables = self._extract_tables_of(join)
        for eq in where.find_all(exp.EQ):
            l_col, r_col = eq.this, eq.expression
            if not (isinstance(l_col, exp.Column) and isinstance(r_col, exp.Column)):
                continue
            l_t, r_t = l_col.table, r_col.table
            if {l_t, r_t} & lhs_tables and {l_t, r_t} & rhs_tables:
                return True
        return False

    def _extract_tables_before(self, select: exp.Select, join: exp.Join) -> set[str]:
        """Tables from FROM + joins positioned before this one."""
        tables = set()
        from_ = select.args.get("from")
        if from_:
            for t in from_.find_all(exp.Table):
                tables.add(t.alias_or_name)
        for j in select.args.get("joins", []) or []:
            if j is join: break
            for t in j.find_all(exp.Table):
                tables.add(t.alias_or_name)
        return tables

    def _extract_tables_of(self, join: exp.Join) -> set[str]:
        return {t.alias_or_name for t in join.find_all(exp.Table)}

    def _make(self, ctx, select, join, confidence, message):
        line_rendered = join.meta.get("line", select.meta.get("line", 1))
        line_source = ctx.node.line_map.get(line_rendered, line_rendered)
        return self.make_finding(
            ctx, line=line_source, column=1,
            message=message,
            code_context=str(join)[:200],
            confidence=confidence,
        )
```

---

## 6. Edge cases

| Case | Emits? |
|---|---|
| `SELECT ... FROM a CROSS JOIN b` | Yes (explicit) |
| `SELECT ... FROM a JOIN b ON a.x = b.y` | No |
| `SELECT ... FROM a JOIN b` (no ON) | Yes (implicit) |
| `SELECT ... FROM a, b WHERE a.x = b.y` | No (WHERE-walk finds predicate) |
| `SELECT ... FROM a, b` (no WHERE) | Yes |
| `SELECT ... FROM a JOIN b USING (x)` | No |
| `SELECT ... CROSS JOIN UNNEST(arr)` | No (exempt) |
| `LATERAL JOIN` | No (exempt) |
| `SELECT ... FROM a CROSS JOIN (SELECT COUNT(*) c FROM x) t` | No (single-row exemption) |
| `SELECT ... FROM a JOIN b -- dbtcov:ignore P001\n  ON TRUE` | No (pragma exempt) |
| WHERE predicate is `BETWEEN`/`IN` subquery rather than `EQ` | Yes (phase-1 limitation — only EQ is walked); documented false-positive |
| Node with `ast=None` | No |

**Known phase-1 false-positive:** `FROM a JOIN b WHERE a.x BETWEEN b.lo AND b.hi` — not detected as connecting. Documented in README; users can add `-- dbtcov:ignore P001`. Phase-2 expands predicate walker to `BETWEEN`/`IN`.

---

## 7. Tests (`tests/unit/analyzers/packs/performance/test_p001.py`)

- Clean JOIN with ON → 0 findings.
- Implicit cross (`FROM a, b` no WHERE) → 1 finding.
- Explicit CROSS JOIN → 1 finding with confidence 0.95.
- Comma-join with connecting WHERE → 0 findings.
- CROSS JOIN UNNEST → 0 findings.
- Single-row subquery cross → 0 findings.
- Pragma `-- dbtcov:ignore P001` → 0 findings.
- Multi-statement / multi-JOIN SELECT with one bad and one good → 1 finding on the bad join.
- `node.ast is None` → 0 findings.
- BETWEEN-connected WHERE → 1 finding (documented limitation).

---

## 8. Acceptance criteria

- [ ] Registered via entry point, discovered by `Registry.discover_rules()`.
- [ ] `pytest tests/unit/analyzers/packs/performance/test_p001.py` ≥95% line + branch coverage.
- [ ] Finding line numbers correctly resolve through `line_map`.
- [ ] `ruff`, `mypy --strict` clean.
- [ ] No imports of sqlglot.optimizer (this rule uses raw AST only; optimizer lives in SPEC-18).

---

## 9. Open questions

- Should pragma parsing be centralized (a shared `SuppressionManager` used by all rules)? **Proposal:** defer — SPEC-07 can host a cross-rule pragma resolver in phase 2 when more rules need it. Inline check for P001 is fine for now.
