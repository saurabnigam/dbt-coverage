"""SPEC-31 §5 — ``dbtcov baseline`` subcommands."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from dbt_coverage import __version__
from dbt_coverage.analyzers import capture_baseline, load_baseline_for
from dbt_coverage.cli.orchestrator import scan as run_scan


@click.group("baseline")
def baseline_cmd() -> None:
    """Manage the .dbtcov/baseline.json waiver snapshot."""


@baseline_cmd.command("capture")
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(),
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Baseline JSON path (default: <project>/.dbtcov/baseline.json).",
)
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--project-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
def capture_cmd(
    path: Path,
    out: Path | None,
    config: Path | None,
    project_config: Path | None,
) -> None:
    """Run a scan and write every non-suppressed finding to the baseline file."""
    try:
        bundle = run_scan(path, config_path=config, project_config=project_config)
    except Exception as e:  # noqa: BLE001
        click.secho(f"dbtcov: fatal: {e}", fg="red", err=True)
        sys.exit(1)

    captured_at = datetime.now(tz=timezone.utc).isoformat()
    payload = capture_baseline(
        bundle.result.findings,
        captured_at=captured_at,
        dbtcov_version=__version__,
    )

    if out is None:
        out = bundle.result.project_root / ".dbtcov" / "baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    n = len(payload["entries"])
    click.echo(f"dbtcov: wrote {n} baselined findings to {out}")


@baseline_cmd.command("diff")
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(),
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--project-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
def diff_cmd(
    path: Path,
    baseline_path: Path | None,
    config: Path | None,
    project_config: Path | None,
) -> None:
    """Print fingerprints added / removed vs the baseline file."""
    try:
        bundle = run_scan(
            path,
            config_path=config,
            project_config=project_config,
            baseline_path=baseline_path,
        )
    except Exception as e:  # noqa: BLE001
        click.secho(f"dbtcov: fatal: {e}", fg="red", err=True)
        sys.exit(1)

    baseline = load_baseline_for(bundle.result.project_root, baseline_path)
    if baseline is None:
        click.echo("dbtcov: no baseline found; nothing to diff.")
        sys.exit(0)

    baseline_fps = {e.fingerprint for e in baseline.entries}
    current_fps = {f.fingerprint for f in bundle.result.findings}

    added = sorted(current_fps - baseline_fps)
    removed = sorted(baseline_fps - current_fps)

    click.echo(f"Baseline: {len(baseline_fps)} entries")
    click.echo(f"Current:  {len(current_fps)} findings")
    click.echo(f"Added:    {len(added)}")
    click.echo(f"Removed:  {len(removed)}")
    for fp in added[:20]:
        click.echo(f"  + {fp}")
    for fp in removed[:20]:
        click.echo(f"  - {fp}")
    if len(added) + len(removed) > 40:
        click.echo("  ... (truncated)")
