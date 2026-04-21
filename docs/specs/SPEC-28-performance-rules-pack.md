# SPEC-28 — Performance rules pack (P002–P010)

## 1. Scope

Nine rules that flag quadratic / unbounded / wasteful SQL patterns. Each rule documents the complexity symptom in its `description` field so users see the Big-O framing.

All rules: `Category.PERFORMANCE`, mostly `TIER_2_WARN`. Each rule file under `src/dbt_coverage/analyzers/packs/performance/`.

## 2. Rules

| ID   | Pattern | Tier | Symptom |
|------|---------|------|---------|
| P002 | Non-sargable predicate `WHERE CAST/UPPER/LOWER(col)=…` | TIER_2_WARN | O(N) scan |
| P003 | Self-join on inequality only (no equality on unique key) | TIER_1_ENFORCED | O(N²) |
| P004 | Unbounded window (`ROWS/RANGE BETWEEN UNBOUNDED … AND UNBOUNDED …`) | TIER_2_WARN | O(N) per row |
| P005 | `COUNT(DISTINCT …) OVER (…)` | TIER_2_WARN | O(N) per row per partition |
| P006 | Fan-out join (join on non-unique key without following GROUP BY / QUALIFY) | TIER_1_ENFORCED | row explosion |
| P007 | `ORDER BY` inside a CTE or subquery without `LIMIT` | TIER_2_WARN | wasted sort |
| P008 | Deep CTE chain (> 8 CTEs) | TIER_2_WARN | optimiser barrier |
| P009 | Over-referenced view (materialised as `view`, `indegree > 5`) | TIER_2_WARN | recomputed N times |
| P010 | Incremental without `unique_key` + `incremental_strategy` | TIER_1_ENFORCED | duplicates on run |

## 3. Message template

Each finding message is prefixed with `[O(…)]` so users see the Big-O hint in consoles and SARIF:

```
[O(N²) risk] Self-join on inequality key without an equality predicate. {file}:{line}
```

## 4. Parameters

Every rule exposes its threshold as `rules.P00N.params.<name>` (e.g. `rules.P008.params.max_depth`).

## 5. Tests

`tests/unit/analyzers/packs/performance/test_p00N_*.py` — one per rule. Positive / negative fixtures use minimal hand-written SQL.
