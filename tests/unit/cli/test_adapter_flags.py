"""Unit tests for the generic adapter CLI flags (SPEC-21 §8)."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.cli.commands._shared import _build_adapter_overrides


def test_generic_enable_disable():
    out = _build_adapter_overrides(
        dbt_artifacts=None,
        dbt_artifacts_dir=None,
        sqlfluff_report=None,
        run_sqlfluff=None,
        enabled_adapters=("sqlfluff", "dbt-test"),
        disabled_adapters=("elementary",),
    )
    assert out["sqlfluff"]["enabled"] is True
    assert out["dbt-test"]["enabled"] is True
    assert out["elementary"]["enabled"] is False


def test_adapter_report_and_mode_kv_parsing():
    out = _build_adapter_overrides(
        dbt_artifacts=None,
        dbt_artifacts_dir=None,
        sqlfluff_report=None,
        run_sqlfluff=None,
        adapter_reports=("sqlfluff=.dbtcov/sqlfluff.json", "dbt-test=target/run_results.json"),
        adapter_modes=("sqlfluff=run", "dbt-test=AUTO"),
    )
    assert out["sqlfluff"]["report"] == ".dbtcov/sqlfluff.json"
    assert out["sqlfluff"]["mode"] == "run"
    assert out["dbt-test"]["report"] == "target/run_results.json"
    # mode is lower-cased so it round-trips through AdapterMode(value)
    assert out["dbt-test"]["mode"] == "auto"


def test_shorthands_merge_with_generic_for_same_adapter():
    """Using --sqlfluff-report + --adapter-mode on the same adapter merges into one block."""
    out = _build_adapter_overrides(
        dbt_artifacts=None,
        dbt_artifacts_dir=None,
        sqlfluff_report=Path("already.json"),
        run_sqlfluff=None,
        adapter_modes=("sqlfluff=run",),
    )
    # The shorthand set mode=read (because a report was given), but the generic
    # --adapter-mode=run overrides it afterwards.
    assert out["sqlfluff"]["report"] == "already.json"
    assert out["sqlfluff"]["mode"] == "run"


def test_malformed_entries_are_skipped(capsys):
    out = _build_adapter_overrides(
        dbt_artifacts=None,
        dbt_artifacts_dir=None,
        sqlfluff_report=None,
        run_sqlfluff=None,
        adapter_reports=("missing-equals", "=no-name", "good=value"),
    )
    assert out == {"good": {"report": "value"}}
    captured = capsys.readouterr()
    assert "malformed" in captured.err
