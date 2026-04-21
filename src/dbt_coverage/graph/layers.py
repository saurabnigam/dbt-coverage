"""SPEC-27 §2 — layer classification helpers.

``classify_layer`` returns the first layer whose glob patterns match the
model's file path or node name (``model.<pkg>.<name>``); ``None`` when no
layer matches. ``edge_is_allowed`` wraps ``ArchitectureConfig.allowed_edges``
for cheap lookup.

Patterns support two forms:
* ``sources.*`` — matched against ``node_id`` using ``fnmatch`` semantics.
* ``staging/**``, ``stg_*`` — matched against the POSIX file path and the
  model name respectively. Path matches are anchored anywhere in the path so
  ``models/staging/foo.sql`` matches ``staging/**``.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_coverage.utils.config import ArchitectureConfig


def classify_layer(
    node_id: str,
    file_path: Path | str,
    config: ArchitectureConfig,
) -> str | None:
    """Return the layer name this node belongs to, or ``None`` if unclassified.

    Iteration order follows ``config.layers`` dict order so users can shape
    priority by listing more specific layers first.
    """
    path = PurePosixPath(str(file_path).replace("\\", "/"))
    model_name = _model_name(node_id)
    for layer, patterns in config.layers.items():
        for pat in patterns:
            if _matches(pat, node_id, model_name, path):
                return layer
    return None


def edge_is_allowed(
    from_layer: str | None,
    to_layer: str | None,
    config: ArchitectureConfig,
) -> bool:
    """Edges where either side is unclassified are allowed (conservative)."""
    if from_layer is None or to_layer is None:
        return True
    for pair in config.allowed_edges:
        if len(pair) == 2 and pair[0] == from_layer and pair[1] == to_layer:
            return True
    return False


def _model_name(node_id: str) -> str:
    # node_id looks like ``model.<pkg>.<name>`` or ``source.<pkg>.<src>.<tbl>``.
    parts = node_id.split(".")
    return parts[-1] if parts else node_id


def _matches(pattern: str, node_id: str, model_name: str, path: PurePosixPath) -> bool:
    if fnmatch.fnmatch(node_id, pattern) or fnmatch.fnmatch(model_name, pattern):
        return True
    # Path globs: anchored to any suffix of the path. ``staging/**`` matches
    # ``models/staging/foo.sql`` via scanning every right-aligned suffix.
    parts = path.parts
    for i in range(len(parts)):
        sub = PurePosixPath(*parts[i:])
        if fnmatch.fnmatch(str(sub), pattern):
            return True
    return False
