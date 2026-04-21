"""SPEC-12 §5 — `dbtcov init` subcommand."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import click


@click.command("init")
@click.option(
    "--at",
    "at",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to create dbtcov.yml in (default: current working directory).",
)
@click.option("--force", is_flag=True, help="Overwrite existing dbtcov.yml")
def init_cmd(at: Path | None, force: bool) -> None:
    """Scaffold a `dbtcov.yml` in the target directory."""
    target_dir = Path(at) if at else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / "dbtcov.yml"
    if dest.exists() and not force:
        raise click.ClickException(f"{dest} exists; use --force to overwrite.")
    template = resources.files("dbt_coverage.templates").joinpath("dbtcov.yml.template")
    dest.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    click.echo(f"Wrote {dest}")
