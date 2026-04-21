"""SPEC-18 §4.1 — AnalysisGraph: DAG + canonical AST cache + column diff."""

from __future__ import annotations

import logging
from typing import Any

from sqlglot import diff as sqlglot_diff

from dbt_coverage.core import ColumnDiff, ParsedNode
from dbt_coverage.scanners import ProjectIndex

from .canonical import canonicalize
from .columns import declared_vs_actual
from .dag import DAG

_LOG = logging.getLogger(__name__)


class AnalysisGraph:
    def __init__(
        self,
        project_index: ProjectIndex,
        parsed_nodes: dict[str, ParsedNode],
        dialect: str = "postgres",
    ) -> None:
        self.project_index = project_index
        self.parsed_nodes = parsed_nodes
        self.dialect = dialect
        self._dag: DAG | None = None
        self._canon_cache: dict[str, Any | None] = {}
        self._canon_built: set[str] = set()
        self._scan_errors: list[str] = []

    # --------------------------------------------------------------- DAG

    @property
    def dag(self) -> DAG:
        if self._dag is None:
            self._dag = self._build_dag()
        return self._dag

    def _resolve_ref_to_node_id(self, ref_name: str) -> str | None:
        """Resolve a ref name to a node_id via project.models keyed by name."""
        for node_id, entry in self.project_index.models.items():
            if entry.name == ref_name:
                return node_id
        return None

    def _build_dag(self) -> DAG:
        d = DAG()
        for nid in self.project_index.models:
            d.add_node(nid)
        for nid, node in self.parsed_nodes.items():
            for ref in node.refs:
                target = self._resolve_ref_to_node_id(ref)
                if target is None:
                    continue
                d.add_edge(target, nid)
        cycles = d.detect_cycles()
        if cycles:
            self._scan_errors.append(f"DAG contains cycle(s): {cycles}")
        return d

    def get_upstream(self, node_id: str) -> set[str]:
        return self.dag.parents(node_id)

    def get_downstream(self, node_id: str) -> set[str]:
        return self.dag.children(node_id)

    def get_transitive_downstream(self, node_id: str) -> set[str]:
        return self.dag.descendants(node_id)

    def is_leaf(self, node_id: str) -> bool:
        return len(self.dag.children(node_id)) == 0

    # --------------------------------------------------------- canonical AST

    def canonical_ast(self, node_id: str) -> Any | None:
        if node_id in self._canon_built:
            return self._canon_cache.get(node_id)
        node = self.parsed_nodes.get(node_id)
        if node is None or not node.parse_success or node.ast is None:
            self._canon_built.add(node_id)
            self._canon_cache[node_id] = None
            return None
        canon = canonicalize(node.ast, self.dialect)
        self._canon_built.add(node_id)
        self._canon_cache[node_id] = canon
        return canon

    def similarity(self, a_id: str, b_id: str) -> float:
        a = self.canonical_ast(a_id)
        b = self.canonical_ast(b_id)
        if a is None or b is None:
            return 0.0
        try:
            edits = sqlglot_diff(a, b)
        except Exception:
            return 0.0
        a_n = _count_nodes(a)
        b_n = _count_nodes(b)
        denom = max(a_n, b_n, 1)
        raw = 1.0 - (len(edits) / denom)
        return max(0.0, min(1.0, raw))

    # -------------------------------------------------------------- columns

    def declared_vs_actual_columns(self, node_id: str) -> ColumnDiff | None:
        node = self.parsed_nodes.get(node_id)
        if node is None:
            return None
        entry = self.project_index.models.get(node_id)
        yml_meta = entry.yml_meta if entry else None
        return declared_vs_actual(yml_meta, node.ast if node.parse_success else None)

    def is_column_used_downstream(self, node_id: str, col: str) -> bool | None:
        """Phase-1 stub — full column-level lineage lands in SPEC-15."""
        return None

    @property
    def scan_errors(self) -> list[str]:
        if self._dag is None:
            _ = self.dag  # trigger build
        return list(self._scan_errors)


def _count_nodes(tree: Any) -> int:
    try:
        return sum(1 for _ in tree.walk())
    except Exception:
        return 1


def build(
    project_index: ProjectIndex,
    parsed_nodes: dict[str, ParsedNode],
    dialect: str = "postgres",
) -> AnalysisGraph:
    g = AnalysisGraph(project_index, parsed_nodes, dialect=dialect)
    _ = g.dag  # eager DAG build; canonical ASTs stay lazy
    return g
