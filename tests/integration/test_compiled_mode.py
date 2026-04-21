"""SPEC-25 — integration test: scan picks COMPILED mode when target/compiled present."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.cli.orchestrator import scan


def _seed_compiled(project_root: Path) -> int:
    """Write plausible compiled SQL (no Jinja) for each model.

    We don't run ``dbt compile``; we synthesize trivially-parsable SQL that
    references a made-up physical table. The contract is only that each
    source model maps to a compiled file.
    """
    compiled_root = project_root / "target" / "compiled" / "sample_project"
    count = 0
    for sql in (project_root / "models").rglob("*.sql"):
        rel = sql.relative_to(project_root)
        dst = compiled_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            f"-- compiled by dbt (test fixture for {rel})\n"
            "select 1 as id, 2 as amount from dev.raw\n",
            encoding="utf-8",
        )
        count += 1
    return count


def test_auto_mode_prefers_compiled_when_artifacts_present(
    sample_project: Path,
) -> None:
    n = _seed_compiled(sample_project)
    assert n > 0

    bundle = scan(sample_project, cli_overrides={"render": {"mode": "AUTO"}})
    result = bundle.result

    # All models parsed from target/compiled.
    assert result.render_stats.rendered_compiled == n
    assert result.render_stats.rendered_mock == 0
    assert result.render_stats.parse_failed == 0


def test_auto_mode_falls_back_to_mock_when_no_compiled(sample_project: Path) -> None:
    # No target/compiled directory created.
    bundle = scan(sample_project, cli_overrides={"render": {"mode": "AUTO"}})
    result = bundle.result

    assert result.render_stats.rendered_compiled == 0
    assert result.render_stats.rendered_mock > 0


def test_forced_compiled_mode_degrades_gracefully(sample_project: Path) -> None:
    """MODE=COMPILED with no artifacts → still renders via MOCK fallback."""
    bundle = scan(sample_project, cli_overrides={"render": {"mode": "COMPILED"}})
    result = bundle.result

    # CompiledRenderer fell back per-file to MOCK; no crashes, models still discovered.
    assert result.render_stats.total_files > 0
