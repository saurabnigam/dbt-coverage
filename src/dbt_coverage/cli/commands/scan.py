"""SPEC-12 §5 — `dbtcov scan` subcommand."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dbt_coverage.cli.orchestrator import scan as run_scan
from dbt_coverage.quality_gates import GateConfig, evaluate

from ._shared import (
    _build_adapter_overrides,
    build_overrides,
    emit_reports,
    exit_on_fatal,
    print_gate_summary,
)


@click.command("scan")
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(),
    help="Path to dbt project (or subdirectory).",
)
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to dbtcov.yml (default: auto-discover).",
)
@click.option(
    "--project-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to the dbt_project.yml to use. When omitted, dbtcov auto-discovers "
        "it at <path>/dbt_project.yml, <path>/config/dbt_project.yml, or "
        "<path>/conf/dbt_project.yml."
    ),
)
@click.option(
    "--format",
    "formats",
    multiple=True,
    default=("console",),
    help="Output formats: console, json, sarif (repeatable).",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("dbtcov-out"),
    help="Directory for JSON/SARIF reports.",
)
@click.option("--dialect", default=None, help="Override SQL dialect.")
@click.option(
    "--render-mode",
    type=click.Choice(["MOCK", "PARTIAL", "COMPILED", "AUTO", "DBT"], case_sensitive=False),
    default=None,
    help="MOCK (Jinja simulation), COMPILED (read target/compiled), AUTO (pick).",
)
@click.option(
    "--compiled-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Override target/compiled directory (overrides render.compiled_dir).",
)
@click.option(
    "--show-suppressed",
    is_flag=True,
    default=False,
    help="Show waived/baselined findings in console output.",
)
@click.option(
    "--skip-detail",
    type=click.Choice(["summary", "aggregated", "per_pair"], case_sensitive=False),
    default=None,
    help="Detail level for the skipped-checks report (default: aggregated).",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to baseline JSON (default: auto-discover .dbtcov/baseline.json).",
)
@click.option("--confidence-threshold", type=float, default=None)
@click.option(
    "--fail-on",
    type=click.Choice(["tier-1", "tier-2", "never"], case_sensitive=False),
    default=None,
    help="Enable gate evaluation in scan.",
)
@click.option(
    "--dbt-artifacts/--no-dbt-artifacts",
    "dbt_artifacts",
    default=None,
    help="Enable or disable the dbt-test adapter (reads target/manifest.json + run_results.json).",
)
@click.option(
    "--dbt-artifacts-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Directory to read dbt artifacts from (defaults to <project>/target).",
)
@click.option(
    "--sqlfluff-report",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a pre-existing sqlfluff JSON report to ingest.",
)
@click.option(
    "--run-sqlfluff/--no-run-sqlfluff",
    "run_sqlfluff",
    default=None,
    help="Invoke sqlfluff directly (requires sqlfluff on PATH).",
)
@click.option(
    "--adapter",
    "enabled_adapters",
    multiple=True,
    help=(
        "Generic adapter enable switch (repeatable). "
        "Example: --adapter sqlfluff --adapter dbt-test."
    ),
)
@click.option(
    "--no-adapter",
    "disabled_adapters",
    multiple=True,
    help="Generic adapter disable switch (repeatable). Takes precedence over --adapter.",
)
@click.option(
    "--adapter-report",
    "adapter_reports",
    multiple=True,
    help=(
        "Override the report path for an adapter. Syntax: NAME=PATH (repeatable). "
        "Example: --adapter-report sqlfluff=.dbtcov/sqlfluff.json."
    ),
)
@click.option(
    "--adapter-mode",
    "adapter_modes",
    multiple=True,
    help=(
        "Override an adapter's execution mode. Syntax: NAME=read|run|auto (repeatable). "
        "Example: --adapter-mode sqlfluff=run."
    ),
)
@click.option(
    "--list-adapters",
    is_flag=True,
    help="List all registered adapters (built-in + plugin) and exit.",
)
@click.option("--no-color", is_flag=True, help="Disable ANSI color.")
@click.option("-v", "--verbose", count=True)
def scan_cmd(
    path: Path,
    config: Path | None,
    project_config: Path | None,
    formats: tuple[str, ...],
    out_dir: Path,
    dialect: str | None,
    render_mode: str | None,
    compiled_dir: Path | None,
    show_suppressed: bool,
    skip_detail: str | None,
    baseline_path: Path | None,
    confidence_threshold: float | None,
    fail_on: str | None,
    dbt_artifacts: bool | None,
    dbt_artifacts_dir: Path | None,
    sqlfluff_report: Path | None,
    run_sqlfluff: bool | None,
    enabled_adapters: tuple[str, ...],
    disabled_adapters: tuple[str, ...],
    adapter_reports: tuple[str, ...],
    adapter_modes: tuple[str, ...],
    list_adapters: bool,
    no_color: bool,
    verbose: int,
) -> None:
    """Scan a dbt project and emit findings/coverage."""
    import logging

    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    if list_adapters:
        from dbt_coverage.adapters import builtin_adapters

        for a in builtin_adapters():
            runnable = "run+read" if a.is_runnable() else "read-only"
            default_report = a.default_report_path or "-"
            click.echo(
                f"{a.name:<12}  {runnable:<8}  default_report={default_report}  "
                f"({a.display_name})"
            )
        sys.exit(0)

    overrides = build_overrides(
        dialect,
        render_mode,
        confidence_threshold,
        compiled_dir=compiled_dir,
        show_suppressed=show_suppressed or None,
        skip_detail=skip_detail,
    )
    adapter_overrides = _build_adapter_overrides(
        dbt_artifacts=dbt_artifacts,
        dbt_artifacts_dir=dbt_artifacts_dir,
        sqlfluff_report=sqlfluff_report,
        run_sqlfluff=run_sqlfluff,
        enabled_adapters=enabled_adapters,
        disabled_adapters=disabled_adapters,
        adapter_reports=adapter_reports,
        adapter_modes=adapter_modes,
    )
    if adapter_overrides:
        overrides["adapters"] = adapter_overrides

    try:
        bundle = run_scan(
            path,
            config_path=config,
            cli_overrides=overrides,
            project_config=project_config,
            baseline_path=baseline_path,
        )
    except Exception as e:  # noqa: BLE001
        click.secho(f"dbtcov: fatal: {e}", fg="red", err=True)
        sys.exit(1)

    result = bundle.result

    gate_cfg: GateConfig | None = None
    if fail_on is not None and fail_on != "never":
        from dbt_coverage.core import Tier

        bundle.config.gate.fail_on_tier = (
            Tier.TIER_1_ENFORCED if fail_on.lower() == "tier-1" else Tier.TIER_2_WARN
        )
        gate_cfg = GateConfig.from_dbtcov(bundle.config)

    emit_reports(
        result,
        out_dir,
        formats,
        no_color=no_color,
        gate_config=gate_cfg,
        show_suppressed=show_suppressed or bundle.config.reports.show_suppressed,
        console_skip_detail=bundle.config.reports.resolve_skip_detail("console"),
        json_skip_detail=bundle.config.reports.resolve_skip_detail("json"),
        sarif_skip_detail=bundle.config.reports.resolve_skip_detail("sarif"),
    )

    exit_on_fatal(result)

    if gate_cfg is not None:
        gr = evaluate(result, gate_cfg)
        print_gate_summary(gr)
        sys.exit(0 if gr.passed else 1)

    sys.exit(0)
