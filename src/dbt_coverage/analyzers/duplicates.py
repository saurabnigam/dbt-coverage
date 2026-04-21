"""SPEC-16a — R001: near-duplicate model detection via MinHash + sqlglot.diff."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_LOG = logging.getLogger(__name__)


class R001DuplicateModelsRule(BaseRule):
    id: ClassVar[str] = "R001"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.REFACTOR
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Near-duplicate models — consolidation candidate"
    confidence_base: ClassVar[float] = 0.9
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        threshold: float = float(ctx.params.get("threshold", 0.85))
        minhash_threshold: float = float(ctx.params.get("minhash_threshold", 0.5))
        num_perm: int = int(ctx.params.get("num_perm", 128))
        shingle_size: int = int(ctx.params.get("shingle_size", 3))

        candidates: dict[str, tuple[Any, list[str]]] = {}
        for nid in ctx.project.models:
            cast = ctx.graph.canonical_ast(nid)
            if cast is None:
                continue
            toks = _tokenize(cast)
            if len(toks) < shingle_size * 2:
                continue
            candidates[nid] = (cast, toks)

        if len(candidates) < 2:
            return

        # MinHash pre-cluster (datasketch); if import fails fall back to O(n²).
        bucket_pairs: list[tuple[str, str]] = []
        try:
            from datasketch import MinHash, MinHashLSH

            lsh = MinHashLSH(threshold=minhash_threshold, num_perm=num_perm)
            hashes: dict[str, Any] = {}
            for nid, (_, tokens) in candidates.items():
                m = MinHash(num_perm=num_perm)
                for sh in _shingles(tokens, shingle_size):
                    m.update(sh.encode("utf-8"))
                lsh.insert(nid, m)
                hashes[nid] = m
            seen_pairs: set[frozenset[str]] = set()
            for nid, m in hashes.items():
                for other_id in lsh.query(m):
                    if other_id == nid:
                        continue
                    pair = frozenset({nid, other_id})
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    bucket_pairs.append((nid, other_id))
        except Exception as e:
            _LOG.debug("MinHash LSH unavailable (%s); falling back to O(n^2).", e)
            ids = list(candidates.keys())
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    bucket_pairs.append((a, b))

        for a_id, b_id in bucket_pairs:
            a_ast = candidates[a_id][0]
            b_ast = candidates[b_id][0]
            try:
                sim = _sqlglot_similarity(a_ast, b_ast)
            except Exception as e:
                _LOG.debug("similarity failed for %s vs %s: %s", a_id, b_id, e)
                continue
            if sim >= threshold:
                yield from self._emit_pair(ctx, a_id, b_id, sim)

    def _emit_pair(
        self, ctx: RuleContext, a_id: str, b_id: str, similarity: float
    ) -> Iterable[Finding]:
        a_model = ctx.project.models[a_id]
        b_model = ctx.project.models[b_id]
        pair_key = f"dup:{min(a_id, b_id)}:{max(a_id, b_id)}"
        for this_id, other_id in ((a_id, b_id), (b_id, a_id)):
            this_m = ctx.project.models[this_id]
            other_m = ctx.project.models[other_id]
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message=(
                    f"Model '{this_m.name}' is {similarity * 100:.1f}% similar to "
                    f"'{other_m.name}' ({other_m.sql_file.path}). Consider consolidating."
                ),
                code_context=pair_key,
                confidence=similarity,
                file_path_override=Path(this_m.sql_file.path),
            )


# ------------------------------------------------------------ module helpers


def _tokenize(ast: Any) -> list[str]:
    try:
        text = ast.sql(pretty=False)
    except Exception:
        return []
    return [t for t in re.split(r"(\W)", text) if t.strip()]


def _shingles(tokens: list[str], k: int):
    for i in range(len(tokens) - k + 1):
        yield " ".join(tokens[i : i + k])


def _sqlglot_similarity(a: Any, b: Any) -> float:
    from sqlglot import diff as sqldiff

    edits = sqldiff(a, b)
    denom = max(_count_nodes(a), _count_nodes(b), 1)
    raw = 1.0 - (len(edits) / denom)
    return max(0.0, min(1.0, raw))


def _count_nodes(ast: Any) -> int:
    try:
        return sum(1 for _ in ast.walk())
    except Exception:
        return 1
