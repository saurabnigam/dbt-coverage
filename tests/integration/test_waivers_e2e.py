"""SPEC-31 — end-to-end waivers + baseline round trips."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from dbt_coverage.cli.main import cli


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_baseline_capture_writes_snapshot(sample_project: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "baseline.json"
    result = runner.invoke(
        cli,
        [
            "baseline",
            "capture",
            "--path",
            str(sample_project),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "entries" in payload


def test_baseline_suppresses_findings_on_next_scan(
    sample_project: Path, tmp_path: Path
) -> None:
    runner = CliRunner()

    # Pass 1: capture baseline into <project>/.dbtcov/baseline.json.
    baseline_path = sample_project / ".dbtcov" / "baseline.json"
    r0 = runner.invoke(
        cli,
        ["baseline", "capture", "--path", str(sample_project), "--out", str(baseline_path)],
    )
    assert r0.exit_code == 0, r0.output
    assert baseline_path.exists()

    baseline_entries = json.loads(baseline_path.read_text())["entries"]
    if not baseline_entries:
        # No findings to waive - test becomes trivially true.
        return

    # Pass 2: rescan and confirm those same findings are stamped suppressed.
    out_dir = tmp_path / "out"
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
    assert r1.exit_code in (0, 1), r1.output
    payload = json.loads((out_dir / "findings.json").read_text())
    suppressed = [f for f in payload["findings"] if f.get("suppressed")]
    assert suppressed, "baselined findings should come back as suppressed"
    assert all(
        f["suppression"]["source"] == "baseline" for f in suppressed
    ), suppressed


def test_override_in_dbtcov_yml_suppresses_finding(
    sample_project: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    # First, see what rules fire so we can waive them all.
    pre_out = tmp_path / "pre"
    r0 = runner.invoke(
        cli,
        [
            "scan",
            "--path",
            str(sample_project),
            "--format",
            "json",
            "--out",
            str(pre_out),
            "--no-color",
        ],
    )
    assert r0.exit_code in (0, 1)
    pre_payload = json.loads((pre_out / "findings.json").read_text())
    pre_findings = pre_payload["findings"]
    if not pre_findings:
        return

    # Waive everything by wildcard across the whole project.
    _write(
        sample_project / "dbtcov.yml",
        "version: 1\n"
        "overrides:\n"
        '  - waive: ["*"]\n'
        '    paths: ["**/*.sql", "**/*.yml"]\n'
        '    reason: "integration test blanket waiver"\n'
        '    reviewer: "ci"\n',
    )

    post_out = tmp_path / "post"
    r1 = runner.invoke(
        cli,
        [
            "scan",
            "--path",
            str(sample_project),
            "--format",
            "json",
            "--out",
            str(post_out),
            "--no-color",
        ],
    )
    assert r1.exit_code in (0, 1), r1.output
    post_payload = json.loads((post_out / "findings.json").read_text())
    overridden = [
        f
        for f in post_payload["findings"]
        if f.get("suppressed") and f.get("suppression", {}).get("source") == "override"
    ]
    assert overridden, "override block should have suppressed at least one finding"


def test_baseline_diff_prints_added_removed(sample_project: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    baseline = sample_project / ".dbtcov" / "baseline.json"
    runner.invoke(
        cli,
        ["baseline", "capture", "--path", str(sample_project), "--out", str(baseline)],
    )
    r = runner.invoke(
        cli,
        ["baseline", "diff", "--path", str(sample_project)],
    )
    assert r.exit_code == 0, r.output
    assert "Baseline:" in r.output
    assert "Current:" in r.output
