# SPEC-26 — Refactor rules pack (R002–R006)

## 1. Scope

Five duplication / maintainability rules that detect refactor candidates: over-sized models, unused scaffolding, and copy-paste logic. All rules operate on `ParsedNode.ast` and emit findings in the existing `Finding` model.

## 2. Rules

### R002 god-model

- **Category**: `REFACTOR`
- **Tier**: `TIER_2_WARN`
- **Severity**: `MAJOR`
- **Fires**: when a model exceeds all three of:
  - `cte_count > 6`
  - `column_count > 30`
  - `complexity.cc >= 25`
- **Params** (override via `rules.R002.params`):
  - `cte_threshold: 6`
  - `column_threshold: 30`
  - `cc_threshold: 25`
- **Message**: `"God-model: {cte_count} CTEs, {column_count} columns, cc={cc}. Extract to staging + marts."`
- **File**: `src/dbt_coverage/analyzers/packs/refactor/r002_god_model.py`

### R003 single-use CTE

- **Category**: `REFACTOR`, **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Walks `WITH` clauses; any CTE referenced exactly once in the downstream SELECT is flagged with the message *"CTE `{name}` is used once; consider inlining."*
- Skips if `preserve_ctes: true` is in model `config{}` meta.

### R004 dead CTE

- **Category**: `REFACTOR`, **Tier**: `TIER_2_WARN`, **Severity**: `MAJOR`.
- Any CTE defined in a `WITH` clause but **not referenced at all** by any downstream expression in the same model.
- Message: *"CTE `{name}` is defined but never referenced; remove to shrink the plan."*

### R005 duplicate projection expression

- **Category**: `REFACTOR`, **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Uses the canonical-hash utility from [src/dbt_coverage/graph/canonical.py](../../src/dbt_coverage/graph/canonical.py) to fingerprint each non-trivial projection expression across all parsed nodes.
- Fires when the same fingerprint appears in `>= min_occurrences` (default 3) distinct models.
- Emits a project-level finding (one per duplicated expression, on the first occurrence).
- Message: *"Expression `{expr_preview}` appears in {n} models: {models}. Extract to a macro."*
- Param: `min_occurrences: 3`, `min_expr_length: 40` (characters, ignore short projections).

### R006 duplicate CASE ladder

- **Category**: `REFACTOR`, **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Same algorithm as R005 but scoped to `CASE` subtrees only.
- Param: `min_arms: 3` (ignore trivial two-arm CASEs).

## 3. Rule registration

Each rule is registered in [src/dbt_coverage/analyzers/rule_registry.py](../../src/dbt_coverage/analyzers/rule_registry.py) `_builtin_rule_classes()`.

## 4. Tests

One unit test per rule under `tests/unit/analyzers/packs/refactor/`, each with one positive and one negative fixture.
