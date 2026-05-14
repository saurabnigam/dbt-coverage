"""`dbtcov ui` — launch the local web dashboard."""

from __future__ import annotations

from pathlib import Path

import click


@click.command("ui", help="Launch the dbtcov web UI dashboard (requires `pip install dbt-coverage-lib[ui]`).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--port", default=8765, show_default=True, type=int, help="Port to bind.")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for the SQLite db + run artifacts (default: ~/.dbtcov-ui).",
)
@click.option("--reload", is_flag=True, help="Enable uvicorn auto-reload (development).")
def ui_cmd(host: str, port: int, data_dir: Path | None, reload: bool) -> None:
    try:
        import uvicorn
    except ImportError as e:
        raise click.ClickException(
            "FastAPI / uvicorn not installed. Install with:\n"
            "    pip install 'dbt-coverage-lib[ui]'"
        ) from e

    from dbt_coverage_ui.app import create_app

    resolved = (data_dir or Path.home() / ".dbtcov-ui").expanduser()
    resolved.mkdir(parents=True, exist_ok=True)
    app = create_app(data_root=resolved)

    click.echo(f"dbtcov UI starting on http://{host}:{port}  (data: {resolved})")
    uvicorn.run(app, host=host, port=port, reload=reload)
