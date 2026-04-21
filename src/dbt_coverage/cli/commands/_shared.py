"""Shared helpers for the scan/gate commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from dbt_coverage.core import ScanResult
from dbt_coverage.reporters import REPORTERS


def build_overrides(
    dialect: str | None,
    render_mode: str | None,
    confidence_threshold: float | None,
    compiled_dir: Path | None = None,
    show_suppressed: bool | None = None,
    skip_detail: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if dialect is not None:
        out["dialect"] = dialect
    render_block: dict[str, Any] = {}
    if render_mode is not None:
        render_block["mode"] = render_mode.upper()
    if compiled_dir is not None:
        render_block["compiled_dir"] = str(compiled_dir)
    if render_block:
        out["render"] = render_block
    if confidence_threshold is not None:
        out["confidence_threshold"] = float(confidence_threshold)
    reports_block: dict[str, Any] = {}
    if skip_detail is not None:
        reports_block["skip_detail"] = skip_detail
    if show_suppressed is not None:
        reports_block["show_suppressed"] = show_suppressed
    if reports_block:
        out["reports"] = reports_block
    return out


def _parse_kv(entries: tuple[str, ...], kind: str) -> dict[str, str]:
    """Parse ``NAME=VALUE`` CLI option entries into a dict. Ignores malformed ones."""
    parsed: dict[str, str] = {}
    for entry in entries or ():
        if "=" not in entry:
            click.echo(
                f"dbtcov: ignoring malformed --adapter-{kind} value {entry!r} "
                "(expected NAME=VALUE)",
                err=True,
            )
            continue
        name, _, value = entry.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        parsed[name] = value
    return parsed


def _build_adapter_overrides(
    *,
    dbt_artifacts: bool | None,
    dbt_artifacts_dir: Path | None,
    sqlfluff_report: Path | None,
    run_sqlfluff: bool | None,
    enabled_adapters: tuple[str, ...] = (),
    disabled_adapters: tuple[str, ...] = (),
    adapter_reports: tuple[str, ...] = (),
    adapter_modes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Translate CLI adapter flags into the ``adapters.<name>`` override block.

    Both the tool-specific shorthands (``--dbt-artifacts``, ``--sqlfluff-report``) and
    the generic SPEC-21 flags (``--adapter``, ``--adapter-report``, ``--adapter-mode``,
    ``--no-adapter``) feed the same ``adapters.<name>.{enabled,mode,report}`` shape.
    Generic flags win over shorthands for the same adapter+field, since users set them
    explicitly.
    """
    out: dict[str, Any] = {}

    def _block(name: str) -> dict[str, Any]:
        return out.setdefault(name, {})

    # Tool-specific shorthands first.
    if dbt_artifacts is not None or dbt_artifacts_dir is not None:
        block = _block("dbt-test")
        if dbt_artifacts is not None:
            block["enabled"] = dbt_artifacts
        if dbt_artifacts_dir is not None:
            block.setdefault("params", {})
            block["params"]["manifest"] = str(Path(dbt_artifacts_dir) / "manifest.json")
            block["report"] = str(Path(dbt_artifacts_dir) / "run_results.json")

    if sqlfluff_report is not None or run_sqlfluff is not None:
        block = _block("sqlfluff")
        if run_sqlfluff is True:
            block["mode"] = "run"
        elif sqlfluff_report is not None:
            block["mode"] = "read"
        if sqlfluff_report is not None:
            block["report"] = str(sqlfluff_report)

    # Generic flags.
    for name in enabled_adapters or ():
        _block(name)["enabled"] = True
    for name in disabled_adapters or ():
        _block(name)["enabled"] = False

    for name, report in _parse_kv(adapter_reports, "report").items():
        _block(name)["report"] = report

    for name, mode in _parse_kv(adapter_modes, "mode").items():
        _block(name)["mode"] = mode.lower()

    return out


def emit_reports(
    result: ScanResult,
    out_dir: Path,
    formats: tuple[str, ...],
    no_color: bool,
    gate_config: Any | None = None,
    show_suppressed: bool = False,
    console_skip_detail: str = "summary",
    json_skip_detail: str = "aggregated",
    sarif_skip_detail: str = "aggregated",
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for fmt in formats:
        if fmt in seen:
            continue
        seen.add(fmt)
        cls = REPORTERS.get(fmt)
        if cls is None:
            click.echo(f"Unknown format: {fmt}", err=True)
            continue
        if fmt == "console":
            reporter = cls(
                gate_config=gate_config,
                use_color=not no_color,
                show_suppressed=show_suppressed,
                skip_detail=console_skip_detail,
            )
            reporter.emit(result, None)
        elif fmt == "json":
            reporter = cls(skip_detail=json_skip_detail)
            reporter.emit(result, out_dir)
        elif fmt == "sarif":
            reporter = cls(skip_detail=sarif_skip_detail)
            reporter.emit(result, out_dir)
        else:
            reporter = cls()
            reporter.emit(result, out_dir)

    # Always emit a coverage.json slice when writing to disk.
    if any(fmt in ("json", "sarif") for fmt in formats):
        payload = {"coverage": [m.model_dump() for m in result.coverage]}
        (out_dir / "coverage.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


def exit_on_fatal(result: ScanResult) -> None:
    """SPEC-12 §6 fatal-error exit codes."""
    rs = result.render_stats
    if rs.total_files == 0:
        click.secho("No models discovered in project.", fg="red", err=True)
        sys.exit(2)
    if rs.total_files > 0 and rs.parse_failed / rs.total_files >= 0.9:
        click.secho(
            f"{rs.parse_failed}/{rs.total_files} models failed to parse — "
            "likely dialect or config mismatch.",
            fg="red",
            err=True,
        )
        sys.exit(3)


def print_gate_summary(gate_result: Any, err: bool = True) -> None:
    stream = sys.stderr if err else sys.stdout
    if gate_result.passed:
        stream.write(
            f"Gate: PASS ({gate_result.counted_findings} counted, "
            f"{gate_result.suppressed_findings} suppressed)\n"
        )
        return
    stream.write("Gate: FAIL\n")
    for r in gate_result.reasons:
        stream.write(f"  - {r.message}")
        if r.offending:
            stream.write(f" ({', '.join(r.offending)})")
        stream.write("\n")
