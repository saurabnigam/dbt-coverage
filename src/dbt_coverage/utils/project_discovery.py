"""SPEC-02 §4.1 — discover the dbt project and read basic info.

Supports three layouts:

1. Conventional — ``dbt_project.yml`` at the project root.
2. Nested config — ``dbt_project.yml`` inside a ``config/`` or ``conf/``
   subdirectory, with path entries like ``source-paths: ['../models']``.
3. Explicit — the caller passes ``config_path=path/to/dbt_project.yml``.

For (2) we compute an **effective scan root** = the common ancestor of the
config-file directory and every resolved path it references, then rewrite the
path lists so they remain relative to the scan root. Downstream code
(`scanners.source_scanner.scan`) only has to deal with one root.

Design principle #3 (fail gracefully): corrupt YAML, missing ``name``,
unresolved Git merge markers — none of these are fatal. We log a WARN,
recover the project name from ``target/manifest.json`` when available, and
fall back to conventional defaults.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from dbt_coverage.core import ConfigError

_yaml = YAML(typ="safe")
# dbt_project.yml files in mature codebases sometimes have duplicate keys (often
# the product of half-finished refactors or merge-conflict cleanup). dbt-core's
# loader tolerates them and so do we; the last definition wins, matching dbt.
_yaml.allow_duplicate_keys = True
_LOG = logging.getLogger(__name__)

# Subdirectories under the project root to probe when no conventional
# ``dbt_project.yml`` is present at the root.
_NESTED_CONFIG_DIRS: tuple[str, ...] = ("config", "conf")

# `{{ env_var('DBT_MODEL_PATH', '../models') }}` → `'../models'`
_ENV_VAR_DEFAULT = re.compile(
    r"""\{\{\s*env_var\(\s*['"][^'"]+['"]\s*,\s*['"](?P<default>[^'"]+)['"]\s*\)\s*\}\}"""
)


class DbtProjectInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path
    name: str
    profile: str | None = None
    model_paths: list[str] = Field(default_factory=lambda: ["models"])
    test_paths: list[str] = Field(default_factory=lambda: ["tests"])
    macro_paths: list[str] = Field(default_factory=lambda: ["macros"])
    seed_paths: list[str] = Field(default_factory=lambda: ["seeds"])
    target_path: str = "target"
    adapter: str | None = None
    dbt_version_required: str | None = None
    # SPEC-02 §4.1 extended — path to the dbt_project.yml actually used.
    # May differ from ``root`` when the config lives in a ``config/`` or
    # ``conf/`` subdirectory.
    config_path: Path | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_project_root(start: Path, project_config: Path | None = None) -> Path:
    """Return the best "start dir" for the scan.

    When ``project_config`` is given, we return its parent (and ``load_project_info``
    will anchor everything to that file). Otherwise we walk up from ``start``
    looking for any of:

    - ``dbt_project.yml`` directly in a directory we visit,
    - ``config/dbt_project.yml`` or ``conf/dbt_project.yml`` in a visited directory.

    Raises :class:`ConfigError` only when no candidate is found anywhere up the
    tree (we still need *something* to scan).
    """
    if project_config is not None:
        p = Path(project_config).resolve()
        if not p.is_file():
            raise ConfigError(f"--project-config not found: {project_config}")
        return p.parent

    start = Path(start).resolve()
    if start.is_file():
        start = start.parent

    seen: set[Path] = set()
    cur = start
    while True:
        real = cur.resolve()
        if real in seen:
            raise ConfigError(
                f"Symlink cycle detected while searching for dbt_project.yml from {start}"
            )
        seen.add(real)

        if (cur / "dbt_project.yml").is_file():
            return cur.resolve()
        for sub in _NESTED_CONFIG_DIRS:
            if (cur / sub / "dbt_project.yml").is_file():
                # We return the *parent* dir — the scan anchor. The nested
                # file is still discovered by load_project_info below.
                return cur.resolve()

        parent = cur.parent
        if parent == cur:
            raise ConfigError(f"No dbt project found walking up from {start}")
        cur = parent


def load_project_info(
    root: Path, *, project_config: Path | None = None
) -> DbtProjectInfo:
    """Resolve and parse ``dbt_project.yml`` for the project at ``root``.

    Discovery order (first hit wins):

    1. ``project_config`` (explicit, from CLI ``--project-config``).
    2. ``root/dbt_project.yml``.
    3. ``root/config/dbt_project.yml``, ``root/conf/dbt_project.yml``.

    If none of the candidates is parseable, :func:`_fallback_project_info`
    returns a best-effort object using conventional defaults.
    """
    root = Path(root).resolve()

    candidates: list[Path] = []
    if project_config is not None:
        candidates.append(Path(project_config).resolve())
    candidates.append(root / "dbt_project.yml")
    for sub in _NESTED_CONFIG_DIRS:
        candidates.append(root / sub / "dbt_project.yml")

    last_reason: str | None = None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        parsed = _try_load(candidate)
        if parsed is None:
            last_reason = f"invalid YAML in {candidate}"
            continue
        return _build_info(root, candidate, parsed)

    reason = last_reason or f"no dbt_project.yml at {root} or {_NESTED_CONFIG_DIRS}"
    return _fallback_project_info(root, reason)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _try_load(path: Path) -> dict[str, Any] | None:
    """Parse ``path`` as YAML mapping; return ``None`` on any failure."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        _LOG.warning("Could not read %s: %s", path, e)
        return None
    try:
        data = _yaml.load(raw) or {}
    except Exception as e:
        _LOG.warning("Invalid YAML in %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        _LOG.warning("%s did not parse to a mapping", path)
        return None
    return data


def _dual_key(d: dict[str, Any], dashed: str, underscored: str, default: Any) -> Any:
    if underscored in d:
        return d[underscored]
    if dashed in d:
        return d[dashed]
    return default


def _as_str_list(val: Any, default: list[str]) -> list[str]:
    if val is None:
        return default
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(x) for x in val]
    return default


def _strip_env_var(value: str) -> str:
    """Collapse ``{{ env_var('X', 'default') }}`` to ``'default'``.

    dbt_project.yml frequently templates paths with ``env_var`` so the path is
    overridable in CI. We're not a Jinja executor; the fallback value is fine
    for static analysis.
    """
    m = _ENV_VAR_DEFAULT.search(value)
    return m.group("default") if m else value


def _clean_paths(raw: list[str], default: list[str]) -> list[str]:
    """Strip ``env_var`` templating, drop empties, preserve order."""
    out: list[str] = []
    for v in raw:
        s = _strip_env_var(v).strip()
        if s:
            out.append(s)
    return out or list(default)


def _common_ancestor(paths: list[Path]) -> Path:
    """Highest directory that contains every path in ``paths``.

    Falls back to the filesystem root if the paths share no common ancestor
    (e.g. when some sit on a different drive on Windows).
    """
    if not paths:
        return Path("/")
    try:
        return Path(os.path.commonpath([str(p) for p in paths]))
    except ValueError:
        return Path("/")


def _effective_scan_root(config_dir: Path, all_path_refs: list[list[str]]) -> Path:
    """Walk every declared path and return the root that contains them all.

    For ``config_dir = pontus/config`` with ``model_paths = ['../models']``,
    the scan root is ``pontus/`` so that downstream code can use
    ``abs.relative_to(scan_root)`` unconditionally.
    """
    resolved: list[Path] = [config_dir]
    for group in all_path_refs:
        for p in group:
            try:
                resolved.append((config_dir / p).resolve())
            except (OSError, RuntimeError):
                # Path.resolve() on a non-existent path is fine; this catches
                # only pathological cases (e.g. too many symlinks).
                continue
    ancestor = _common_ancestor(resolved)
    return ancestor if ancestor.is_dir() else config_dir


def _rewrite(paths: list[str], config_dir: Path, scan_root: Path) -> list[str]:
    """Translate ``paths`` (relative to ``config_dir``) to be relative to ``scan_root``.

    Preserves a leading ``./`` as bare ``.`` and keeps paths that can't be
    resolved verbatim (scanner will treat them as missing).
    """
    out: list[str] = []
    for p in paths:
        try:
            abs_p = (config_dir / p).resolve()
            rel = abs_p.relative_to(scan_root)
            out.append(str(rel) if str(rel) != "." else p)
        except (ValueError, OSError):
            out.append(p)
    return out


def _build_info(
    start_root: Path, config_path: Path, data: dict[str, Any]
) -> DbtProjectInfo:
    config_dir = config_path.parent.resolve()

    name = data.get("name")
    if not name:
        recovered = (
            _name_from_manifest(start_root)
            or _name_from_manifest(config_dir.parent)
            or start_root.name
        )
        _LOG.warning(
            "dbt_project.yml at %s is missing 'name'; using %r",
            config_path,
            recovered,
        )
        name = recovered or "unknown"

    model_paths_raw = _as_str_list(
        _dual_key(data, "model-paths", "model_paths", None),
        _as_str_list(_dual_key(data, "source-paths", "source_paths", None), ["models"]),
    )
    test_paths_raw = _as_str_list(_dual_key(data, "test-paths", "test_paths", None), ["tests"])
    macro_paths_raw = _as_str_list(_dual_key(data, "macro-paths", "macro_paths", None), ["macros"])
    seed_paths_raw = _as_str_list(
        _dual_key(data, "seed-paths", "seed_paths", None),
        _as_str_list(_dual_key(data, "data-paths", "data_paths", None), ["seeds"]),
    )
    target_path_raw = str(_dual_key(data, "target-path", "target_path", "target"))

    model_paths = _clean_paths(model_paths_raw, ["models"])
    test_paths = _clean_paths(test_paths_raw, ["tests"])
    macro_paths = _clean_paths(macro_paths_raw, ["macros"])
    seed_paths = _clean_paths(seed_paths_raw, ["seeds"])
    target_path = _strip_env_var(target_path_raw).strip() or "target"

    scan_root = _effective_scan_root(
        config_dir,
        [model_paths, test_paths, macro_paths, seed_paths, [target_path]],
    )

    if scan_root != config_dir:
        _LOG.info(
            "dbtcov: config at %s references paths above it; using scan root %s",
            config_path,
            scan_root,
        )

    return DbtProjectInfo(
        root=scan_root,
        name=str(name),
        profile=str(data["profile"]) if "profile" in data else None,
        model_paths=_rewrite(model_paths, config_dir, scan_root),
        test_paths=_rewrite(test_paths, config_dir, scan_root),
        macro_paths=_rewrite(macro_paths, config_dir, scan_root),
        seed_paths=_rewrite(seed_paths, config_dir, scan_root),
        target_path=_rewrite([target_path], config_dir, scan_root)[0],
        adapter=None,  # resolution from profiles.yml is out of scope for phase 1
        dbt_version_required=data.get("require-dbt-version") or data.get("require_dbt_version"),
        config_path=config_path,
    )


# ---------- Fallbacks --------------------------------------------------------


def _name_from_manifest(root: Path) -> str | None:
    """Read ``metadata.project_name`` from ``<root>/target/manifest.json``.

    Authoritative across dbt 1.x, so we use it to keep scanner UIDs aligned
    with the manifest when the YAML name can't be read.
    """
    manifest = Path(root) / "target" / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        with manifest.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # pragma: no cover - defensive
        _LOG.debug("Could not read %s: %s", manifest, e)
        return None
    meta = data.get("metadata") or {}
    name = meta.get("project_name")
    return str(name) if isinstance(name, str) and name else None


def _name_from_yaml_prefix(path: Path) -> str | None:
    """Grep a ``name:`` line before any unparseable block (last-ditch)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^\s*name\s*:\s*['\"]?([A-Za-z0-9_\-]+)['\"]?\s*$", raw, re.M)
    return m.group(1) if m else None


def _fallback_project_info(root: Path, reason: str) -> DbtProjectInfo:
    """Best-effort ``DbtProjectInfo`` when no parseable config is available.

    Anchors the scan root at the directory most likely to contain ``models/``:

    - If ``root`` already has a ``models/`` subdir, keep it.
    - Else, if we're sitting in one of :data:`_NESTED_CONFIG_DIRS` (``config/``,
      ``conf/``) whose parent has a ``models/`` subdir, move the root up.
    """
    scan_root = root
    if not (scan_root / "models").is_dir() and scan_root.name in _NESTED_CONFIG_DIRS:
        parent = scan_root.parent
        if (parent / "models").is_dir():
            scan_root = parent

    name = (
        _name_from_manifest(scan_root)
        or _name_from_manifest(root)
        or _name_from_yaml_prefix(root / "dbt_project.yml")
        or (scan_root.name or "unknown")
    )
    if name == (scan_root.name or "unknown"):
        for sub in _NESTED_CONFIG_DIRS:
            for probe in (root, scan_root):
                nested = probe / sub / "dbt_project.yml"
                grepped = _name_from_yaml_prefix(nested) if nested.is_file() else None
                if grepped:
                    name = grepped
                    break

    _LOG.warning(
        "dbtcov: proceeding with default paths at %s (reason: %s); project name=%r",
        scan_root,
        reason,
        name,
    )
    return DbtProjectInfo(
        root=scan_root,
        name=name,
        profile=None,
        model_paths=["models"],
        test_paths=["tests"],
        macro_paths=["macros"],
        seed_paths=["seeds"],
        target_path="target",
        adapter=None,
        dbt_version_required=None,
        config_path=None,
    )
