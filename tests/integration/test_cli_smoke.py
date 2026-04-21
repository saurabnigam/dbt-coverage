"""SPEC-13 §5 — CLI smoke tests via Click's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from dbt_coverage.cli.main import cli


def test_init_scaffolds_dbtcov_yml(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--at", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "dbtcov.yml").exists()


def test_scan_outputs_json(sample_project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "scan",
            "--path",
            str(sample_project),
            "--format",
            "json",
            "--out",
            str(out_dir),
            "--no-color",
        ],
    )
    assert result.exit_code in (0, 1), result.output
    assert (out_dir / "findings.json").exists()
    payload = json.loads((out_dir / "findings.json").read_text())
    assert "findings" in payload
    assert "coverage" in payload
    assert "render_stats" in payload


def test_scan_then_gate(sample_project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    runner = CliRunner()
    r1 = runner.invoke(
        cli,
        [
            "scan",
            "--path",
            str(sample_project),
            "--format",
            "json",
            "--out",
            str(out_dir),
            "--no-color",
        ],
    )
    assert r1.exit_code in (0, 1)

    r2 = runner.invoke(
        cli,
        [
            "gate",
            "--path",
            str(sample_project),
            "--results",
            str(out_dir / "findings.json"),
        ],
    )
    assert r2.exit_code in (0, 1)
    assert "Gate:" in r2.output


def test_scan_sarif_output(sample_project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "scan",
            "--path",
            str(sample_project),
            "--format",
            "sarif",
            "--out",
            str(out_dir),
            "--no-color",
        ],
    )
    assert result.exit_code in (0, 1), result.output
    assert (out_dir / "findings.sarif").exists()
    doc = json.loads((out_dir / "findings.sarif").read_text())
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["tool"]["driver"]["name"] == "dbtcov"
