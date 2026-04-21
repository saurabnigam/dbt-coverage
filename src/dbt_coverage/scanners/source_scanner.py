"""SPEC-03 §4.2 — walk a dbt project, build ProjectIndex."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from pathlib import Path

from dbt_coverage.utils import DbtcovConfig, DbtProjectInfo

from .project_index import IndexedFile, ModelEntry, ProjectIndex
from .yaml_parser import extract_doc_blocks, parse_schema_yml

_LOG = logging.getLogger(__name__)

_MAX_FILE_BYTES = 10 * 1024 * 1024


def _read_text(abs_path: Path) -> str | None:
    try:
        if abs_path.stat().st_size > _MAX_FILE_BYTES:
            return None
        try:
            return abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return abs_path.read_text(encoding="latin-1")
            except Exception:
                return None
    except OSError:
        return None


def _make_indexed(abs_path: Path, root: Path, content: str) -> IndexedFile:
    rel = abs_path.relative_to(root)
    h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
    return IndexedFile(path=rel, absolute_path=abs_path, content=content, source_hash=h)


def _is_excluded(rel_path: Path, patterns: list[str]) -> bool:
    rel_str = str(rel_path).replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel_str, p) for p in patterns)


def _walk_dir(root: Path, rel_dir: str) -> list[Path]:
    """Walk ``root/rel_dir`` recursively, yielding files (symlinks not followed)."""
    base = root / rel_dir
    out: list[Path] = []
    if not base.exists() or not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
    return out


def _collect_sql(
    root: Path,
    sub_dirs: list[str],
    excludes: list[str],
    errors: list[str],
) -> list[IndexedFile]:
    out: list[IndexedFile] = []
    seen: set[Path] = set()
    for d in sub_dirs:
        for abs_path in _walk_dir(root, d):
            if abs_path in seen:
                continue
            seen.add(abs_path)
            if abs_path.suffix.lower() != ".sql":
                continue
            rel = abs_path.relative_to(root)
            if _is_excluded(rel, excludes):
                continue
            text = _read_text(abs_path)
            if text is None:
                errors.append(f"Skipped unreadable/oversized file: {rel}")
                continue
            out.append(_make_indexed(abs_path, root, text))
    return out


def _collect_yml(
    root: Path, sub_dirs: list[str], excludes: list[str], errors: list[str]
) -> list[IndexedFile]:
    out: list[IndexedFile] = []
    seen: set[Path] = set()
    scan_dirs = list(sub_dirs) + [""]  # "" = project_root itself
    for d in scan_dirs:
        for abs_path in _walk_dir(root, d):
            if abs_path in seen:
                continue
            seen.add(abs_path)
            if abs_path.suffix.lower() not in (".yml", ".yaml"):
                continue
            rel = abs_path.relative_to(root)
            if _is_excluded(rel, excludes):
                continue
            if rel.name == "dbt_project.yml" or rel.name == "dbtcov.yml":
                continue
            text = _read_text(abs_path)
            if text is None:
                errors.append(f"Skipped unreadable YAML: {rel}")
                continue
            out.append(_make_indexed(abs_path, root, text))
    return out


def _collect_md(root: Path, sub_dirs: list[str]) -> dict[str, str]:
    docs: dict[str, str] = {}
    seen: set[Path] = set()
    for d in list(sub_dirs) + [""]:
        for abs_path in _walk_dir(root, d):
            if abs_path in seen:
                continue
            seen.add(abs_path)
            if abs_path.suffix.lower() != ".md":
                continue
            text = _read_text(abs_path)
            if text is None:
                continue
            docs.update(extract_doc_blocks(text))
    return docs


def scan(project_info: DbtProjectInfo, config: DbtcovConfig) -> ProjectIndex:
    """Walk the dbt project and build a fully populated ProjectIndex."""
    root = project_info.root
    excludes = list(config.exclude)
    errors: list[str] = []

    index = ProjectIndex(project_root=root, project_name=project_info.name)

    model_sqls = _collect_sql(root, project_info.model_paths, excludes, errors)
    for f in model_sqls:
        name = f.path.stem
        node_id = f"model.{project_info.name}.{name}"
        if node_id in index.models:
            errors.append(f"Duplicate model name '{name}' — first occurrence kept")
            continue
        index.models[node_id] = ModelEntry(node_id=node_id, name=name, sql_file=f)

    index.singular_tests = _collect_sql(root, project_info.test_paths, excludes, errors)
    index.macros = _collect_sql(root, project_info.macro_paths, excludes, errors)

    # Seeds: record .csv files (content loaded for hashing only).
    seed_files: list[IndexedFile] = []
    for d in project_info.seed_paths:
        for abs_path in _walk_dir(root, d):
            if abs_path.suffix.lower() != ".csv":
                continue
            rel = abs_path.relative_to(root)
            if _is_excluded(rel, excludes):
                continue
            text = _read_text(abs_path)
            if text is None:
                errors.append(f"Skipped unreadable seed: {rel}")
                continue
            seed_files.append(_make_indexed(abs_path, root, text))
    index.seeds = seed_files

    yml_files = _collect_yml(
        root, project_info.model_paths + project_info.test_paths + [""], excludes, errors
    )
    index.yml_files = yml_files

    # Parse all YAML files.
    for yf in yml_files:
        models, sources, exposures, warns = parse_schema_yml(yf.path, yf.content)
        for w in warns:
            errors.append(w)
        for m in models:
            # Link by model name to existing SQL entries.
            linked = False
            for node_id, entry in index.models.items():
                if entry.name == m.name:
                    if entry.yml_meta is None:
                        entry.yml_meta = m
                        linked = True
                    else:
                        errors.append(f"Model '{m.name}' has duplicate YAML definitions")
                        linked = True
                    break
            if not linked:
                errors.append(
                    f"YAML in {yf.path} declares model '{m.name}' with no matching .sql file"
                )
        for s in sources:
            key = (s.source_name, s.table_name)
            index.sources[key] = s
        index.exposures.extend(exposures)

    index.doc_blocks = _collect_md(root, project_info.model_paths)
    index.scan_errors = errors
    return index
