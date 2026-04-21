# SPEC-29 — Quality rules pack extensions (Q004–Q007)

## 1. Scope

Four metadata / naming rules. Q004 and Q005 require no AST — they operate on `ProjectIndex` YAML metadata — so they run even when parsing fails.

| ID   | Name | Tier | Source |
|------|------|------|--------|
| Q004 | Missing model description | TIER_2_WARN | schema.yml `models[].description` |
| Q005 | Undocumented column | TIER_2_WARN | projection list vs schema.yml `columns[]` |
| Q006 | Naming convention | TIER_1_ENFORCED | layer classifier + model name prefix |
| Q007 | Inconsistent column casing | TIER_2_WARN | AST projection casing split |

## 2. Q004 — missing model description

- Fires when a model's schema.yml entry lacks `description` or the description is empty / only whitespace.
- `applies_to_node = True`, `requires_ast = False`.
- Message: *"Model `{name}` has no description in schema.yml."*

## 3. Q005 — undocumented column

- Requires AST (otherwise projection list is unknown).
- For each projection column that's not in the model's declared `columns:` list, emit one finding.
- Param: `ignore_prefixes: ["__", "_"]` — columns starting with `_` are considered internal and skipped.

## 4. Q006 — naming convention

- Tier-1, severity MAJOR.
- Compares the layer (from [SPEC-27](SPEC-27-architecture-rules-pack.md) `architecture.layers`) with the model name prefix:
  - `staging` layer requires `stg_` prefix
  - `intermediate` layer requires `int_`
  - `mart` layer requires `fct_` / `dim_`
- Override patterns allowed via `rules.Q006.params.layer_prefixes`.

## 5. Q007 — inconsistent column casing

- Inspects projection aliases. If the set contains both snake_case and camelCase / PascalCase, emit one finding.
- Param: `dominant_casing: auto` (auto-detect majority; recommend the minority renames).

## 6. Tests

One unit test per rule under `tests/unit/analyzers/packs/quality/`.
