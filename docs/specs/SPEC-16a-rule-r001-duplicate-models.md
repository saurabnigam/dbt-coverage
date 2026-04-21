# SPEC-16a — Rule R001: Near-Duplicate Model Detection

**Status:** draft
**Depends on:** SPEC-01, SPEC-07, SPEC-18
**Blocks:** none

---

## 1. Purpose

Flag pairs of models whose **canonical ASTs** are ≥ threshold similar (default 0.85). These are refactor candidates — typically a fork of a model that was copy-pasted and drifted slightly, or two marts computing almost-identical aggregates.

This is a **project-level rule** — it needs all models together, not one at a time. `applies_to_node = False`.

---

## 2. Detection pipeline

1. **Collect** all `(node_id, canonical_ast)` pairs from `AnalysisGraph` where `canonical_ast is not None`.
2. **Pre-cluster** via MinHash over canonical token stream — O(n) bucketing. Two nodes fall in the same bucket if Jaccard similarity of their token shingles ≥ `minhash_threshold` (default 0.5).
3. **Pairwise confirm** within each bucket via `sqlglot.diff(a, b)` → edits list. Compute:
   ```
   similarity = 1 - len(edits) / max(count_nodes(a), count_nodes(b))
   ```
4. **Emit findings** for pairs above `threshold` (default 0.85). Each pair emits **two** findings (one per node pointing at the other) for reviewer visibility — dedup at fingerprint level already avoids true duplicates.

**Why MinHash pre-cluster:** without it, duplicate check is O(n²) × O(sqlglot.diff). For a 500-model project that's 125k × ~10ms = 20min. MinHash drops candidate pairs to ~O(n).

---

## 3. Module layout

```
src/dbt_coverage/analyzers/
  duplicates.py            # R001DuplicateModelsRule + helpers
```

---

## 4. API Surface

### 4.1 `duplicates.py`

```python
from sqlglot import diff as sqldiff
from datasketch import MinHash, MinHashLSH
from dbt_coverage.analyzers.rule_base import BaseRule
from dbt_coverage.core import Severity, Category, FindingType, Tier

class R001DuplicateModelsRule(BaseRule):
    id = "R001"
    default_severity = Severity.MAJOR
    default_tier = Tier.TIER_1_ENFORCED
    category = Category.REFACTOR
    finding_type = FindingType.CODE_SMELL
    description = "Near-duplicate models — consolidation candidate"
    confidence_base = 0.9
    applies_to_node = False
    requires_ast = True

    # params (overridable in dbtcov.yml):
    #   threshold: float = 0.85
    #   minhash_threshold: float = 0.5
    #   num_perm: int = 128
    #   shingle_size: int = 3

    def check(self, ctx):
        threshold = ctx.params.get("threshold", 0.85)
        minhash_threshold = ctx.params.get("minhash_threshold", 0.5)
        num_perm = ctx.params.get("num_perm", 128)
        shingle_size = ctx.params.get("shingle_size", 3)

        # 1. Collect canonical ASTs
        candidates: dict[str, tuple] = {}   # node_id -> (canonical_ast, tokens)
        for nid, node in ctx.project.models.items():
            cast = ctx.graph.canonical_ast(nid)
            if cast is None:
                continue
            tokens = _tokenize(cast)
            if len(tokens) < shingle_size * 2:   # too small to be meaningful
                continue
            candidates[nid] = (cast, tokens)

        if len(candidates) < 2:
            return

        # 2. MinHash pre-cluster
        lsh = MinHashLSH(threshold=minhash_threshold, num_perm=num_perm)
        hashes: dict[str, MinHash] = {}
        for nid, (_, tokens) in candidates.items():
            m = MinHash(num_perm=num_perm)
            for shingle in _shingles(tokens, shingle_size):
                m.update(shingle.encode())
            lsh.insert(nid, m)
            hashes[nid] = m

        # 3. Pairwise confirm within buckets (dedupe unordered pairs)
        seen_pairs: set[frozenset[str]] = set()
        for nid, m in hashes.items():
            for other_id in lsh.query(m):
                if other_id == nid:
                    continue
                pair = frozenset({nid, other_id})
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                a_ast, _ = candidates[nid]
                b_ast, _ = candidates[other_id]
                sim = _sqlglot_similarity(a_ast, b_ast)
                if sim >= threshold:
                    yield from self._emit_pair(ctx, nid, other_id, sim)

    def _emit_pair(self, ctx, a_id, b_id, similarity):
        a_model = ctx.project.models[a_id]
        b_model = ctx.project.models[b_id]
        for this_id, other_id in [(a_id, b_id), (b_id, a_id)]:
            this_m = ctx.project.models[this_id]
            other_m = ctx.project.models[other_id]
            yield self.make_finding(
                ctx,
                line=1, column=1,
                message=(f"Model '{this_m.name}' is {similarity*100:.1f}% similar to "
                         f"'{other_m.name}' ({other_m.sql_file.path}). Consider consolidating."),
                code_context=f"dup:{min(a_id, b_id)}:{max(a_id, b_id)}",  # stable cross-pair fingerprint
                confidence=similarity,
                file_path_override=this_m.sql_file.path,   # override ctx.node (None for project-rules)
            )

def _tokenize(ast) -> list[str]:
    """Stable, order-preserving token stream over a canonical AST."""
    # sqlglot's sql() is deterministic on a canonical tree; split on whitespace + punctuation.
    import re
    return [t for t in re.split(r"(\W)", ast.sql(pretty=False)) if t.strip()]

def _shingles(tokens: list[str], k: int):
    for i in range(len(tokens) - k + 1):
        yield " ".join(tokens[i : i + k])

def _sqlglot_similarity(a, b) -> float:
    edits = sqldiff(a, b)
    return 1 - len(edits) / max(_count_nodes(a), _count_nodes(b), 1)

def _count_nodes(ast) -> int:
    return sum(1 for _ in ast.walk())
```

**Note on `file_path_override` and `make_finding`:** project-level rules pass `ctx.node=None`, so `BaseRule.make_finding` must accept an optional override path. Add this parameter to SPEC-07's `BaseRule.make_finding` before implementation (tracked as amendment in §8).

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| Project has 0 or 1 models | No findings |
| All models `parse_success=False` | No findings (canonical_ast None) |
| Two models identical → similarity 1.0 | Two findings (one per node) emitted |
| Three copies of same model (A, B, C all similar) | 3 pairs → 6 findings — acceptable; users see the cluster |
| Very small models (<6 tokens) | Skipped (too small to be meaningful) |
| MinHash bucket huge (e.g. many `stg_*` models with boilerplate) | Threshold 0.5 on MinHash keeps buckets tight; pairwise confirm still filters. Benchmark on 500-model project < 10s target. |
| `threshold` overridden to 0.99 | Only near-exact copies flagged |
| Two models share 90% of SQL but use different `ref` targets | Still flagged — lineage differences aren't similarity-penalized since canonical AST normalizes table aliases. Acceptable: the point is structural similarity. |
| Similarity computation raises (rare sqlglot bug) | Caught, pair skipped, logged |

---

## 6. Tests (`tests/unit/analyzers/test_r001_duplicates.py`)

- 0 models → no findings.
- 2 identical models → 2 findings, similarity 1.0, mutual cross-reference in messages.
- 2 models 90% similar (1 column different in SELECT) → 2 findings.
- 2 models 50% similar → 0 findings (below default threshold).
- 3 mutual copies → 6 findings (3 pairs × 2).
- Override threshold to 0.5 via `ctx.params` → more findings.
- 500 synthetic models (bench): duplicates scan completes in <10s.
- MinHash pre-cluster actually reduces comparison count (verified by instrumented counter).
- One model has `parse_success=False` → excluded cleanly.

---

## 7. Acceptance criteria

- [ ] Rule registered via entry point, discoverable.
- [ ] `pytest tests/unit/analyzers/test_r001_duplicates.py` ≥90% coverage.
- [ ] Synthetic 500-model benchmark < 10s; pre-cluster demonstrably reduces pairwise calls (asserted in test).
- [ ] Two identical models produce deterministic finding fingerprints (same pair always emits same code_context hash).
- [ ] `ruff`, `mypy --strict` clean.
- [ ] `datasketch` added to runtime deps.

---

## 8. Spec amendments to SPEC-07

To support this rule, SPEC-07's `BaseRule.make_finding` signature adds:

```python
def make_finding(
    self, ctx, line, column, message,
    *,
    code_context: str,
    confidence: float | None = None,
    severity_override: Severity | None = None,
    tier_override: Tier | None = None,
    file_path_override: Path | None = None,   # NEW — for project-level rules
    end_line=None, end_column=None,
) -> Finding: ...
```

If `ctx.node is None`, `file_path_override` is required (assertion).

---

## 9. Open questions

- Should similarity be reported on a per-pair basis (current: two findings per pair), or once per pair with a "linked finding" structure? **Proposal:** keep two findings; simpler for reporters, no SARIF-specific linking needed, and each finding is actionable from its own file's perspective.
- Pair-cluster merging (A~B, B~C, A~C → "cluster of 3")? **Proposal:** phase 2, cosmetic — raw pairs are correct, cluster presentation is a reporter concern.
