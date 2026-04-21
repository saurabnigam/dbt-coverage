"""Unit tests for dbt project discovery (SPEC-02 §4.1).

These exercise the three supported layouts:

1. Conventional — ``dbt_project.yml`` at the root.
2. Nested — ``<root>/config/dbt_project.yml`` or ``<root>/conf/dbt_project.yml``.
3. Corrupt — fallback that keeps the scan running on default paths.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dbt_coverage.utils import find_project_root, load_project_info


def _mk_model(dst: Path, name: str, sql: str = "select 1 as a") -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / f"{name}.sql").write_text(sql, encoding="utf-8")


def test_conventional_layout_still_works(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text(
        textwrap.dedent(
            """
            name: 'simple'
            profile: 'local'
            config-version: 2
            model-paths: ['models']
            """
        ),
        encoding="utf-8",
    )
    _mk_model(tmp_path / "models", "m1")

    root = find_project_root(tmp_path)
    info = load_project_info(root)

    assert info.name == "simple"
    assert info.root == tmp_path.resolve()
    assert info.model_paths == ["models"]


def test_nested_config_dir_discovered(tmp_path: Path) -> None:
    """A ``config/dbt_project.yml`` with ``../models`` paths should be found
    and the effective scan root should be bumped up to ``tmp_path``."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "dbt_project.yml").write_text(
        textwrap.dedent(
            """
            name: 'nested'
            profile: 'pp'
            config-version: 2
            source-paths: ['../models']
            macro-paths: ['../macros']
            test-paths: ['../tests']
            target-path: 'target'
            """
        ),
        encoding="utf-8",
    )
    _mk_model(tmp_path / "models", "orders")

    root = find_project_root(tmp_path)
    info = load_project_info(root, project_config=cfg_dir / "dbt_project.yml")

    assert info.name == "nested"
    # Root should be bumped up so ``models/`` resolves locally.
    assert info.root == tmp_path.resolve()
    assert info.model_paths == ["models"]
    assert info.test_paths == ["tests"]
    assert info.macro_paths == ["macros"]
    assert info.config_path == (cfg_dir / "dbt_project.yml").resolve()


def test_env_var_default_is_extracted(tmp_path: Path) -> None:
    """``{{ env_var('X', 'default') }}`` paths collapse to their default."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "dbt_project.yml").write_text(
        textwrap.dedent(
            """
            name: 'templated'
            config-version: 2
            source-paths: ["{{ env_var('DBT_MODEL_PATH', '../models') }}"]
            """
        ),
        encoding="utf-8",
    )
    _mk_model(tmp_path / "models", "stub")

    info = load_project_info(tmp_path, project_config=cfg_dir / "dbt_project.yml")
    assert info.model_paths == ["models"]
    assert info.root == tmp_path.resolve()


def test_duplicate_keys_are_tolerated(tmp_path: Path) -> None:
    """dbt_project.yml with duplicate keys (e.g. from half-finished merges)
    should still load — the last definition wins, matching dbt-core."""
    (tmp_path / "dbt_project.yml").write_text(
        textwrap.dedent(
            """
            name: 'dup'
            config-version: 2
            models:
              dup:
                tags: ['a']
                tags: ['b']
            """
        ),
        encoding="utf-8",
    )
    info = load_project_info(tmp_path)
    assert info.name == "dup"


def test_corrupt_yaml_falls_back_and_finds_name_from_manifest(tmp_path: Path) -> None:
    """When dbt_project.yml is unparseable but ``target/manifest.json`` has a
    ``project_name``, the fallback must use that name (so adapter UIDs line up).
    """
    (tmp_path / "dbt_project.yml").write_text(
        "<<<<<<< HEAD\nname: foo\n=======\nname: bar\n>>>>>>> branch\n",
        encoding="utf-8",
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text(
        '{"metadata": {"project_name": "real_name", "adapter_type": "snowflake"}}',
        encoding="utf-8",
    )
    info = load_project_info(tmp_path)
    assert info.name == "real_name"
    assert info.root == tmp_path.resolve()


def test_project_config_override_rejects_missing_file(tmp_path: Path) -> None:
    from dbt_coverage.core import ConfigError

    with pytest.raises(ConfigError):
        find_project_root(tmp_path, project_config=tmp_path / "nope.yml")
