"""SPEC-12 §5 — `dbtcov gate` subcommand."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from dbt_coverage.core import ScanResult
from dbt_coverage.quality_gates import GateConfig, evaluate
from dbt_coverage.utils import find_project_root, load_config

from ._shared import print_gate_summary


@click.command("gate")
@click.option(
    "--results",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to previously emitted findings.json (from `dbtcov scan`).",
)
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(),
    help="Project root (used for auto-discovering dbtcov.yml).",
)
def gate_cmd(results: Path, config: Path | None, path: Path) -> None:
    """Evaluate a previously computed ScanResult against the gate."""
    try:
        payload = json.loads(Path(results).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        click.secho(f"Could not read results: {e}", fg="red", err=True)
        sys.exit(2)

    try:
        result = ScanResult.model_validate(payload)
    except Exception as e:  # noqa: BLE001
        click.secho(f"Results file is not a valid ScanResult: {e}", fg="red", err=True)
        sys.exit(2)

    try:
        project_root = find_project_root(Path(path))
    except Exception:  # noqa: BLE001
        project_root = Path(path).resolve()

    dbtcov_cfg = load_config(project_root, config_path=config, cli_overrides=None)
    gate_cfg = GateConfig.from_dbtcov(dbtcov_cfg)

    gr = evaluate(result, gate_cfg)
    print_gate_summary(gr, err=False)
    sys.exit(0 if gr.passed else 1)
