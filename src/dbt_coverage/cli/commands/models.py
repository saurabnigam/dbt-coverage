"""SPEC-12 §5 — `dbtcov models` subcommand: per-model assessment table."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from dbt_coverage.core import ScanResult


@click.command("models")
@click.option(
    "--results",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("dbtcov-out/findings.json"),
    show_default=True,
    help="Path to findings.json produced by `dbtcov scan`.",
)
@click.option(
    "--sort",
    type=click.Choice(["score", "name", "tier"], case_sensitive=False),
    default="score",
    show_default=True,
    help="Sort order: score (worst first), name (alpha), tier (T1 first).",
)
@click.option(
    "--min-score",
    type=click.IntRange(0, 100),
    default=None,
    help="Only show models with score ≤ this value (e.g. --min-score 70 = at-risk).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["console", "json"], case_sensitive=False),
    default="console",
    show_default=True,
    help="Output format.",
)
@click.option("--no-color", is_flag=True, help="Disable ANSI color.")
def models_cmd(
    results: Path,
    sort: str,
    min_score: int | None,
    fmt: str,
    no_color: bool,
) -> None:
    """Show a per-model quality score table.

    Reads findings.json written by `dbtcov scan --format json`.
    Each model gets a 0–100 score based on test coverage, doc coverage,
    parse success and severity of findings.

    \b
    Score penalties:
      -30  no test declared in schema.yml
      -20  doc coverage < 50% for this model
      -30  any TIER_1_ENFORCED finding
      -10  any TIER_2_WARN finding
      -10  SQL parse failed (AST checks skipped)
    """
    try:
        payload = json.loads(Path(results).read_text(encoding="utf-8"))
    except Exception as e:
        click.secho(f"Could not read results: {e}", fg="red", err=True)
        sys.exit(2)

    try:
        result = ScanResult.model_validate(payload)
    except Exception as e:
        click.secho(f"Not a valid ScanResult: {e}", fg="red", err=True)
        sys.exit(2)

    if not result.model_summaries:
        click.secho(
            "No model_summaries in this findings.json.\n"
            "Re-run `dbtcov scan` to regenerate with per-model data.",
            fg="yellow",
            err=True,
        )
        sys.exit(2)

    rows = list(result.model_summaries)

    # Apply --min-score filter
    if min_score is not None:
        rows = [r for r in rows if r.score <= min_score]

    # Apply --sort
    if sort == "score":
        rows.sort(key=lambda r: (r.score, r.name))
    elif sort == "name":
        rows.sort(key=lambda r: r.name)
    elif sort == "tier":
        rows.sort(key=lambda r: (0 if r.tier1_rules else 1 if r.tier2_rules else 2, r.name))

    if fmt == "json":
        out = [r.model_dump() for r in rows]
        sys.stdout.write(json.dumps(out, indent=2))
        sys.stdout.write("\n")
        return

    # Console output.
    #
    # Rich's ``Console.size`` only honours a constructor-supplied ``width``
    # when ``height`` is *also* passed — otherwise it falls through to live
    # terminal detection and returns 80 cols in non-TTY contexts, which
    # causes Rich to silently drop narrow columns from tables. We therefore
    # pin both dimensions here. Column widths total ~164 at max
    # (5+42+5+5+5+28+60 + 7*2 padding), so 200 leaves room for elastic
    # columns while matching common CI terminals.
    term = shutil.get_terminal_size(fallback=(200, 24))
    width = max(term.columns, 200)
    console = Console(
        color_system="auto" if not no_color else None,
        no_color=no_color,
        highlight=False,
        width=width,
        height=max(term.lines, 24),
    )

    total = len(result.model_summaries)
    shown = len(rows)
    at_risk = sum(1 for r in result.model_summaries if r.score < 70)
    perfect = sum(1 for r in result.model_summaries if r.score == 100)

    console.print(
        f"\n[bold]Model Assessment[/bold]  "
        f"[dim]{result.project_name or 'unknown'}  "
        f"total={total}  shown={shown}  "
        f"at-risk(<70)={at_risk}  perfect(100)={perfect}[/dim]\n"
    )

    tbl = Table(
        show_header=True,
        header_style="bold",
        show_lines=False,
        box=None,
        padding=(0, 1),
    )
    # Rich will happily drop entire narrow columns if it can't fit the whole
    # table (e.g. when stdout is piped). Locking both ``min_width`` and
    # ``max_width`` on the fixed-size columns marks them as incompressible,
    # so Rich truncates the elastic Model/File columns instead.
    tbl.add_column("Score", min_width=5, max_width=5, justify="right")
    tbl.add_column("Model", min_width=30, max_width=42, overflow="ellipsis")
    tbl.add_column("Tests", min_width=5, max_width=5, justify="center")
    # SPEC-32 §8 — dedicated Unit column (✓ ≥1 unit test, ✗ 0, — dbt<1.8).
    tbl.add_column("Unit", min_width=4, max_width=4, justify="center")
    tbl.add_column("Docs", min_width=5, max_width=5, justify="right")
    tbl.add_column("Parse", min_width=5, max_width=5, justify="center")
    # SPEC-33 §6 — Skips column surfaces the count of rule checks that didn't
    # run for this model. ``-`` = none; yellow ``N`` = at least one skip.
    tbl.add_column("Skips", min_width=5, max_width=5, justify="right")
    tbl.add_column("Findings", min_width=18, max_width=28, overflow="ellipsis")
    tbl.add_column("File", min_width=20, max_width=60, overflow="ellipsis")

    for row in rows:
        score_text = Text(str(row.score), style=_score_style(row.score))

        test_cell = Text("✓" if row.test_covered else "✗", style="green" if row.test_covered else "red")

        unit_count = getattr(row, "unit_test_count", 0)
        data_count = getattr(row, "data_test_count", 0)
        if unit_count > 0:
            unit_cell = Text("✓", style="green")
        elif data_count > 0 or unit_count == 0:
            unit_cell = Text("✗", style="yellow")
        else:
            unit_cell = Text("—", style="dim")

        doc_pct = f"{row.doc_ratio * 100:.0f}%"
        doc_style = "green" if row.doc_ratio >= 0.8 else "yellow" if row.doc_ratio >= 0.5 else "red"
        doc_cell = Text(doc_pct, style=doc_style)

        if row.parse_success:
            parse_cell = Text("✓", style="green")
        elif row.render_uncertain:
            parse_cell = Text("~", style="yellow")
        else:
            parse_cell = Text("✗", style="red")

        findings_parts: list[str] = []
        for r in row.tier1_rules:
            findings_parts.append(f"[red]{r}[/red]")
        for r in row.tier2_rules:
            findings_parts.append(f"[yellow]{r}[/yellow]")
        findings_cell = Text.from_markup(", ".join(findings_parts) if findings_parts else "[dim]-[/dim]")

        skip_n = getattr(row, "skip_count", 0)
        if skip_n == 0:
            skip_cell = Text("-", style="dim")
        else:
            skip_cell = Text(str(skip_n), style="yellow")

        tbl.add_row(
            score_text,
            row.name,
            test_cell,
            unit_cell,
            doc_cell,
            parse_cell,
            skip_cell,
            findings_cell,
            Text(row.file_path, style="dim"),
        )

    console.print(tbl)

    # Legend
    console.print(
        "\n[dim]Score: 100=perfect  <70=at-risk  0=failing | "
        "Parse: ✓=full AST  ~=uncertain (Jinja unresolved)  ✗=failed | "
        "Tests: declared DATA tests  |  Unit: ≥1 dbt unit_test  |  "
        "Skips: rule checks that did not run for this model[/dim]\n"
    )


def _score_style(score: int) -> str:
    if score >= 90:
        return "green"
    if score >= 70:
        return "yellow"
    return "red"
