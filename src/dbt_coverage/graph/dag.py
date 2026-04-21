"""SPEC-18 §4.2 — simple adjacency-list DAG."""

from __future__ import annotations


class DAG:
    def __init__(self) -> None:
        self._children: dict[str, set[str]] = {}
        self._parents: dict[str, set[str]] = {}
        self._nodes: set[str] = set()

    def add_node(self, node_id: str) -> None:
        self._nodes.add(node_id)
        self._children.setdefault(node_id, set())
        self._parents.setdefault(node_id, set())

    def add_edge(self, parent: str, child: str) -> None:
        self.add_node(parent)
        self.add_node(child)
        self._children[parent].add(child)
        self._parents[child].add(parent)

    def nodes(self) -> set[str]:
        return set(self._nodes)

    def children(self, node_id: str) -> set[str]:
        return set(self._children.get(node_id, set()))

    def parents(self, node_id: str) -> set[str]:
        return set(self._parents.get(node_id, set()))

    def descendants(self, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for child in self._children.get(cur, set()):
                if child in seen:
                    continue
                seen.add(child)
                stack.append(child)
        return seen

    def ancestors(self, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for parent in self._parents.get(cur, set()):
                if parent in seen:
                    continue
                seen.add(parent)
                stack.append(parent)
        return seen

    def detect_cycles(self) -> list[list[str]]:
        """Return a list of simple cycles (DFS). Best-effort; large graphs may not enumerate all cycles."""
        cycles: list[list[str]] = []
        color: dict[str, int] = {n: 0 for n in self._nodes}
        stack: list[str] = []

        def dfs(n: str) -> None:
            color[n] = 1
            stack.append(n)
            for c in self._children.get(n, set()):
                if color.get(c, 0) == 1:
                    try:
                        idx = stack.index(c)
                        cycles.append(stack[idx:] + [c])
                    except ValueError:
                        cycles.append([c])
                elif color.get(c, 0) == 0:
                    dfs(c)
            stack.pop()
            color[n] = 2

        for n in list(self._nodes):
            if color[n] == 0:
                dfs(n)
        return cycles
