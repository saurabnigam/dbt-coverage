"""SPEC-12 §5 — `dbtcov` CLI entry point."""

from __future__ import annotations

import click

from .commands.baseline import baseline_cmd
from .commands.gate import gate_cmd
from .commands.init import init_cmd
from .commands.models import models_cmd
from .commands.scan import scan_cmd


@click.group(help="dbtcov — Data Quality Control Plane for dbt projects.")
@click.version_option(package_name="dbt-coverage-lib")
def cli() -> None:
    pass


cli.add_command(init_cmd)
cli.add_command(scan_cmd)
cli.add_command(gate_cmd)
cli.add_command(models_cmd)
cli.add_command(baseline_cmd)


def main() -> None:
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
