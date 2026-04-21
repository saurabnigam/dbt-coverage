# SPEC-27 — Architecture rules pack (A001–A005)

## 1. Scope

Five structural rules that enforce layer hygiene and single-responsibility at the DAG level. Introduces `Category.ARCHITECTURE` and a layer-classifier utility.

## 2. New category + config

`Category.ARCHITECTURE = "ARCHITECTURE"` added to [src/dbt_coverage/core/enums.py](../../src/dbt_coverage/core/enums.py).

New config block in [src/dbt_coverage/utils/config.py](../../src/dbt_coverage/utils/config.py):

```yaml
architecture:
  layers:
    source: ["sources.*"]
    staging: ["stg_*", "staging/**"]
    intermediate: ["int_*", "intermediate/**"]
    mart: ["fct_*", "dim_*", "marts/**"]
  allowed_edges:
    - [source, staging]
    - [staging, intermediate]
    - [staging, mart]
    - [intermediate, mart]
```

Layer classification utility lives at `src/dbt_coverage/graph/layers.py`:

```python
def classify_layer(node_id: str, file_path: Path, config: ArchitectureConfig) -> str | None
def edge_is_allowed(from_layer: str, to_layer: str, config: ArchitectureConfig) -> bool
```

## 3. Rules

### A001 layer violation

- **Category**: `ARCHITECTURE`, **Tier**: `TIER_1_ENFORCED`, **Severity**: `MAJOR`.
- Walks `AnalysisGraph` edges; flags any edge whose `(from_layer, to_layer)` is not in `allowed_edges`.
- Message: *"Layer violation: `{from}` ({from_layer}) → `{to}` ({to_layer}) is not in allowed_edges."*

### A002 fan-in explosion

- **Tier**: `TIER_2_WARN`, **Severity**: `MAJOR`.
- Per-model. `indegree(model) > threshold` (default 15).
- Message: *"Fan-in: {n} upstream models. Model is doing too much; consider splitting."*
- Param: `threshold: 15`.

### A003 direct source bypass

- **Tier**: `TIER_1_ENFORCED`, **Severity**: `MAJOR`.
- Fires when a model in a layer `>= intermediate` calls `source(...)` directly — staging is supposed to be the only layer that reads sources.
- Message: *"{layer} model `{name}` reads sources directly; route through staging."*

### A004 circular layer dependency

- **Tier**: `TIER_1_ENFORCED`, **Severity**: `CRITICAL`.
- DFS over the layer-classified edges looking for cycles *within or across layers*.
- Emits one finding per cycle with the cycle path in the message.

### A005 leaky abstraction

- **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Staging model exposes a column whose name is uppercase or exactly matches the raw source column name (heuristic: all-upper or mixed-case names in projection list).
- Message: *"Staging model exposes raw column `{col}`; rename to snake_case."*

## 4. Tests

`tests/unit/analyzers/packs/architecture/test_architecture_pack.py` with a handwritten DAG fixture covering every layer and at least one violation per rule.
