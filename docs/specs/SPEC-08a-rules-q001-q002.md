# SPEC-08a — Rule Pack: Q001 `SELECT *` + Q002 Missing PK Test

**Status:** draft
**Depends on:** SPEC-01, SPEC-06, SPEC-07, SPEC-18
**Blocks:** none (one of the vertical-slice terminals)

---

## 1. Purpose

Implement the first two quality rules:

- **Q001** — `SELECT *` in a non-source model or in a CTE.
- **Q002** — Model missing primary-key test (`unique` + `not_null` on inferred PK columns).

These two are chosen for vertical-slice validation: Q001 exercises AST walking; Q002 exercises YAML introspection. Together they validate the full rule pipeline with minimal detection logic.

---

## 2. Non-goals

- No auto-fix generation (phase 3).
- No Q003–Q010 rules (phase 2).
- No dialect-specific `SELECT *` variants (e.g. `SELECT * EXCEPT (...)` on BigQuery handled as Q001 exempt — see §5).

---

## 3. Module layout

```
src/dbt_coverage/analyzers/packs/
  __init__.py
  quality/
    __init__.py
    q001_select_star.py
    q002_missing_pk.py
```

---

## 4. Q001 — `SELECT *` in non-source model or CTE

### 4.1 Detection

Walk AST for `sqlglot.expressions.Star` nodes under any `Select`:
- **Trigger:** Star found in any SELECT's `expressions` list.
- **Exempt contexts:**
  - Top-level SELECT where the `FROM` is a single `__SRC_*__` identifier (i.e. `SELECT * FROM {{ source(...) }}` — staging models legitimately do this).
  - `COUNT(*)`, `EXISTS(SELECT 1 FROM ...)` — not triggered by `Star` in these positions (sqlglot models them differently).
  - BigQuery `SELECT * EXCEPT (col1, col2)` — sqlglot parses this as a modified Star; treat as exempt (rationale: user is explicit about exclusions).

### 4.2 Rule class

```python
from sqlglot import expressions as exp
from dbt_coverage.analyzers.rule_base import BaseRule
from dbt_coverage.core import Severity, Category, FindingType, Tier

class Q001SelectStarRule(BaseRule):
    id = "Q001"
    default_severity = Severity.MAJOR
    default_tier = Tier.TIER_2_WARN        # plan says T2 in capability matrix
    category = Category.QUALITY
    finding_type = FindingType.CODE_SMELL
    description = "SELECT * in non-source model or CTE"
    confidence_base = 0.95
    applies_to_node = True
    requires_ast = True

    def check(self, ctx):
        if ctx.node.ast is None:
            return
        for select in ctx.node.ast.find_all(exp.Select):
            for expr in select.expressions:
                if not isinstance(expr, exp.Star):
                    continue
                if self._is_exempt(select, expr):
                    continue
                # sqlglot carries line info on most nodes via meta
                line_rendered = expr.meta.get("line", select.meta.get("line", 1))
                line_source = ctx.node.line_map.get(line_rendered, line_rendered)
                yield self.make_finding(
                    ctx,
                    line=line_source,
                    column=1,
                    message="SELECT * in model or CTE; list columns explicitly",
                    code_context=str(select)[:200],
                )

    def _is_exempt(self, select: exp.Select, star: exp.Star) -> bool:
        # BigQuery SELECT * EXCEPT
        if star.args.get("except"):
            return True
        # Top-level source-only select: SELECT * FROM __SRC_*__ with no joins, no CTE
        from_ = select.args.get("from")
        if from_ and not select.find(exp.Join) and not select.find(exp.CTE):
            tables = [t for t in from_.find_all(exp.Table)]
            if len(tables) == 1 and tables[0].name.startswith("__SRC_"):
                return True
        return False
```

### 4.3 Edge cases

| Case | Emits? |
|---|---|
| `SELECT * FROM __SRC_raw_events__` (top-level, single source) | No (exempt) |
| `SELECT * FROM __REF_stg_events__` (top-level, single ref) | **Yes** (ref target, not a source) |
| `SELECT * FROM a JOIN b` | Yes |
| `WITH x AS (SELECT * FROM t) SELECT a FROM x` | Yes (CTE uses *) |
| `SELECT COUNT(*) FROM t` | No (count star, different node) |
| `SELECT * EXCEPT (pii_col) FROM t` (BigQuery) | No (explicit exclusion) |
| Node with `ast=None` | No (skip silently) |
| Two SELECT * in same query | Two findings, different lines |

### 4.4 Tests (`tests/unit/analyzers/packs/quality/test_q001.py`)

- Plain `SELECT * FROM tbl` → 1 finding, line=1.
- `SELECT * FROM __SRC_raw_events__` → 0 findings.
- `SELECT * FROM __REF_stg_events__` → 1 finding.
- CTE with `SELECT *` → 1 finding, line matches CTE body.
- `SELECT * EXCEPT (a) FROM t` parsed as bigquery → 0 findings.
- `node.ast is None` → 0 findings, no crash.
- Two SELECT * in same query → 2 distinct findings with different lines.

---

## 5. Q002 — Missing Primary-Key Test

### 5.1 Detection

A "primary key test" = a column has both `unique` AND `not_null` generic tests in `schema.yml`. The PK column is inferred by:
- If YAML `meta.primary_key: <col>` is set → that column.
- Else if a column named `id` or `<model_name>_id` exists → that column.
- Else → **no inferrable PK** → emit an INFO-severity finding suggesting the user set `meta.primary_key`, but don't fail Tier-1 (see §5.3).

### 5.2 Rule class

```python
class Q002MissingPkRule(BaseRule):
    id = "Q002"
    default_severity = Severity.CRITICAL
    default_tier = Tier.TIER_1_ENFORCED
    category = Category.QUALITY
    finding_type = FindingType.BUG
    description = "Model missing primary-key test (unique + not_null)"
    confidence_base = 1.0     # YAML introspection, high confidence
    applies_to_node = True
    requires_ast = False      # YAML-only; runs even on render_uncertain nodes

    def check(self, ctx):
        yml = ctx.project.models.get(ctx.node_id, None)
        yml = yml.yml_meta if yml else None
        if yml is None:
            return      # Q003 handles "no YAML at all"

        pk_candidates = self._infer_pk(yml)
        if not pk_candidates:
            yield self.make_finding(
                ctx, line=yml.line, column=1,
                message=f"Model '{yml.name}' has no inferrable primary key. "
                        f"Declare `meta: {{primary_key: <col>}}` in schema.yml.",
                code_context=f"model:{yml.name}",
                confidence=0.7,           # lower — we're inferring
                severity_override=Severity.MINOR,
                tier_override=Tier.TIER_2_WARN,
            )
            return

        for col_name in pk_candidates:
            col = next((c for c in yml.columns if c.name == col_name), None)
            if col is None:
                yield self.make_finding(
                    ctx, line=yml.line, column=1,
                    message=f"Inferred PK column '{col_name}' not listed in schema.yml",
                    code_context=f"model:{yml.name}:pk:{col_name}",
                    confidence=0.8,
                )
                continue
            has_unique = any(self._is_test(t, "unique") for t in col.tests)
            has_not_null = any(self._is_test(t, "not_null") for t in col.tests)
            if not (has_unique and has_not_null):
                missing = [name for flag, name in
                           [(has_unique, "unique"), (has_not_null, "not_null")]
                           if not flag]
                yield self.make_finding(
                    ctx, line=yml.line, column=1,
                    message=(f"PK column '{col_name}' in model '{yml.name}' "
                             f"is missing {' + '.join(missing)} test(s)"),
                    code_context=f"model:{yml.name}:pk:{col_name}",
                )

    def _infer_pk(self, yml) -> list[str]:
        pk = yml.meta.get("primary_key")
        if isinstance(pk, str): return [pk]
        if isinstance(pk, list): return pk
        col_names = {c.name for c in yml.columns}
        for candidate in ("id", f"{yml.name}_id"):
            if candidate in col_names: return [candidate]
        return []

    @staticmethod
    def _is_test(test_entry, name: str) -> bool:
        if isinstance(test_entry, str): return test_entry == name
        if isinstance(test_entry, dict): return name in test_entry
        return False
```

### 5.3 Edge cases

| Case | Emits? | Notes |
|---|---|---|
| Model has no `yml_meta` | No | Q003 handles (phase 2) |
| PK inferred as `id`, column has `unique` + `not_null` | No | Fully tested |
| PK inferred as `id`, only `unique` present | Yes | Message names missing `not_null` |
| PK is `meta.primary_key: composite_key` where `composite_key: [a, b]` | Yes (two findings if both missing tests) | Compound PK iteration |
| No PK inferrable | Yes | But **downgraded** to MINOR / Tier-2 (see block in `check`) |
| Source table (not a model) | No | Rule skipped — ctx is per-model only |
| Test declared on YAML row as `- unique: {config: {severity: warn}}` (dict form) | Detected | `_is_test` handles dict form |

**Why downgrade "no inferrable PK" to Tier-2:** heuristic inference is fragile; false-positive risk on intermediate models without natural keys. Users who care can explicitly set `meta.primary_key` to promote their model under the strict rule.

### 5.4 Tests (`tests/unit/analyzers/packs/quality/test_q002.py`)

- Model with `id` column having both tests → 0 findings.
- Model with `id` column having only `unique` → 1 finding naming `not_null`.
- Model with `meta.primary_key: user_id`, `user_id` has both tests → 0 findings.
- Model with `meta.primary_key: [a, b]`, `a` has tests, `b` missing → 1 finding about `b`.
- Model with no PK-like column and no meta → 1 Tier-2 finding suggesting `meta.primary_key`.
- Model with `yml_meta=None` → 0 findings.
- Test declared via dict form `- unique: {config: ...}` → correctly detected.

---

## 6. Acceptance criteria

- [ ] Both rules register via `discover_rules()` and appear with correct `id`/`severity`/`tier`.
- [ ] `pytest tests/unit/analyzers/packs/quality/` ≥95% line coverage.
- [ ] Run against `examples/basic_project` emits expected findings (documented in SPEC-13 golden file).
- [ ] Q001 finding line numbers correspond to source-file lines (line_map translation verified).
- [ ] Q002 handles all 4 YAML test-declaration forms (string, dict, configured dict, list-of-mixed).
- [ ] `ruff`, `mypy --strict` clean.
- [ ] Rule execution time <5ms/model for both combined (benchmark, not gate).

---

## 7. Open questions

- Should Q001 distinguish CTE-internal `SELECT *` from top-level? **Proposal:** same message, different severity tier — MAJOR for top-level, MINOR for CTE. **Decision:** keep single MAJOR in phase 1, tier adjustable by user; revisit if CTE noise becomes a complaint.
- Q002 inference may be wrong for event-stream models with composite surrogate keys. Document `meta.primary_key: [...]` override as the escape hatch in README.
