"""SPEC-03 §4.3 — parse schema.yml files preserving source line numbers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .project_index import YamlColumnMeta, YamlModelMeta, YamlSourceMeta

_yaml = YAML(typ="rt")  # round-trip mode preserves line numbers via .lc attr


def _block_line(block: Any, default: int = 1) -> int:
    """Extract 1-indexed line number from a ruamel block (CommentedMap/Seq)."""
    try:
        lc = getattr(block, "lc", None)
        if lc is not None and getattr(lc, "line", None) is not None:
            return int(lc.line) + 1
    except Exception:
        pass
    return default


def _coerce_column(col_raw: Any) -> YamlColumnMeta | None:
    if not isinstance(col_raw, dict):
        return None
    name = col_raw.get("name")
    if not isinstance(name, str):
        return None
    return YamlColumnMeta(
        name=name,
        description=col_raw.get("description"),
        tests=list(col_raw.get("tests") or col_raw.get("data_tests") or []),
        meta=dict(col_raw.get("meta") or {}),
        tags=[str(t) for t in (col_raw.get("tags") or [])],
    )


def parse_schema_yml(
    path: Path,
    content: str,
) -> tuple[list[YamlModelMeta], list[YamlSourceMeta], list[dict[str, Any]], list[str]]:
    """Parse one schema.yml.

    Returns: ``(models, sources, exposures, warnings)``. Warnings are non-fatal
    human-readable strings appended to ``ProjectIndex.scan_errors`` by the caller.
    """
    warnings: list[str] = []
    models: list[YamlModelMeta] = []
    sources: list[YamlSourceMeta] = []
    exposures: list[dict[str, Any]] = []

    try:
        data = _yaml.load(content)
    except Exception as e:
        warnings.append(f"{path}: YAML parse error: {e}")
        return models, sources, exposures, warnings

    if not isinstance(data, dict):
        return models, sources, exposures, warnings

    for model_block in data.get("models") or []:
        if not isinstance(model_block, dict):
            continue
        name = model_block.get("name")
        if not isinstance(name, str):
            continue
        cols: list[YamlColumnMeta] = []
        for c in model_block.get("columns") or []:
            coerced = _coerce_column(c)
            if coerced is not None:
                cols.append(coerced)
        # dbt uses both `tests:` and `data_tests:` (dbt 1.8+).
        model_tests = list(model_block.get("tests") or model_block.get("data_tests") or [])
        models.append(
            YamlModelMeta(
                name=name,
                description=model_block.get("description"),
                columns=cols,
                tests=model_tests,
                meta=dict(model_block.get("meta") or {}),
                tags=[str(t) for t in (model_block.get("tags") or [])],
                config=dict(model_block.get("config") or {}),
                unit_tests=list(model_block.get("unit_tests") or []),
                file_path=path,
                line=_block_line(model_block),
            )
        )

    for src_block in data.get("sources") or []:
        if not isinstance(src_block, dict):
            continue
        src_name = src_block.get("name")
        if not isinstance(src_name, str):
            continue
        for tbl_block in src_block.get("tables") or []:
            if not isinstance(tbl_block, dict):
                continue
            tbl_name = tbl_block.get("name")
            if not isinstance(tbl_name, str):
                continue
            cols2: list[YamlColumnMeta] = []
            for c in tbl_block.get("columns") or []:
                coerced = _coerce_column(c)
                if coerced is not None:
                    cols2.append(coerced)
            sources.append(
                YamlSourceMeta(
                    source_name=src_name,
                    table_name=tbl_name,
                    description=tbl_block.get("description"),
                    columns=cols2,
                    meta=dict(tbl_block.get("meta") or {}),
                    file_path=path,
                    line=_block_line(tbl_block),
                )
            )

    for exp_block in data.get("exposures") or []:
        if isinstance(exp_block, dict):
            exposures.append(dict(exp_block))

    return models, sources, exposures, warnings


_DOC_RE = re.compile(r"{%\s*docs\s+(\w+)\s*%}(.*?){%\s*enddocs\s*%}", re.DOTALL)


def extract_doc_blocks(content: str) -> dict[str, str]:
    """Pull `{% docs name %}...{% enddocs %}` blocks out of a markdown file."""
    out: dict[str, str] = {}
    for match in _DOC_RE.finditer(content):
        name = match.group(1)
        body = match.group(2).strip()
        out[name] = body
    return out
