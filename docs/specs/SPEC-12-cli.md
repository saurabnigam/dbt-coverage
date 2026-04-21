# SPEC-12 — CLI (`init`, `scan`, `gate`)

**Status:** draft
**Depends on:** SPEC-01, SPEC-02, SPEC-03, SPEC-05, SPEC-06, SPEC-07, SPEC-09a, SPEC-10a, SPEC-11, SPEC-18
**Blocks:** —

---

## 1. Purpose

Expose the end-to-end pipeline as three phase-1 subcommands:

- `dbtcov init` — scaffold `dbtcov.yml`.
- `dbtcov scan [PATH]` — full scan, emit reports, no gate exit-code decision.
- `dbtcov gate [PATH]` — scan + gate, non-zero exit on failure.

Additional phase-1 commands (`rules`, `report`) are listed but may ship as a follow-up if time pressure demands it.

---

## 2. Non-goals

- No `dbtcov fix` (phase 3, LiteLLM).
- No `dbtcov duplicates` / `dbtcov dead-columns` standalone commands (phase 2).
- No `--changed-only` incremental mode (phase 2, needs SPEC-19).
- No interactive TUI — all output is stdout/stderr; rich for coloring only.
- No auto-update check.
- No telemetry / analytics.

---

## 3. Module layout

```
src/dbt_coverage/cli/
  __init__.py
  main.py                   # click entrypoint `dbtcov`
  commands/
    __init__.py
    init.py                 # `dbtcov init`
    scan.py                 # `dbtcov scan`
    gate.py                 # `dbtcov gate`  (imports scan logic, adds gate evaluation)
    rules.py                # `dbtcov rules`  — ship if time permits
    report.py               # `dbtcov report` — ship if time permits
  orchestrator.py           # glue: scan() pipeline function (reused by scan + gate)
```

**Entrypoint registration in `pyproject.toml`:**
```toml
[project.scripts]
dbtcov = "dbt_coverage.cli.main:cli"
```

---

## 4. Top-level CLI

```python
# main.py
import click
from dbt_coverage import __version__
from dbt_coverage.cli.commands import init, scan, gate

@click.group()
@click.version_option(__version__, prog_name="dbtcov")
@click.option("--log-level", type=click.Choice(["DEBUG","INFO","WARNING","ERROR"]),
              default="INFO", envvar="DBTCOV_LOG_LEVEL")
@click.option("--no-color", is_flag=True, help="Disable ANSI color output")
@click.pass_context
def cli(ctx, log_level, no_color):
    """dbtcov — data quality control plane for dbt projects."""
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level
    ctx.obj["no_color"] = no_color
    _configure_logging(log_level)

cli.add_command(init.init_cmd)
cli.add_command(scan.scan_cmd)
cli.add_command(gate.gate_cmd)
```

**Global options apply to all subcommands.** Log level also honors `DBTCOV_LOG_LEVEL` env var for CI ergonomics.

---

## 5. `dbtcov init`

Scaffolds `dbtcov.yml` in the current working directory (or `--at` path) using the template shipped at `templates/dbtcov.yml.template`.

```python
@click.command("init")
@click.option("--at", type=click.Path(path_type=Path), default=Path.cwd(),
              help="Directory to create dbtcov.yml in")
@click.option("--force", is_flag=True, help="Overwrite existing dbtcov.yml")
def init_cmd(at, force):
    dest = Path(at) / "dbtcov.yml"
    if dest.exists() and not force:
        raise click.ClickException(f"{dest} exists; use --force to overwrite.")
    template = importlib.resources.files("dbt_coverage.templates").joinpath("dbtcov.yml.template")
    dest.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    click.echo(f"Wrote {dest}")
```

**Template content** (shipped in `src/dbt_coverage/templates/dbtcov.yml.template`):
```yaml
version: 1
render:
  mode: mock                  # mock | partial | dbt
  fallback: partial
dialect: snowflake            # override if not detected from dbt_project.yml
confidence_threshold: 0.7
rules:
  "Q001": { enabled: true, severity: MAJOR,    tier: TIER_2_WARN }
  "Q002": { enabled: true, severity: CRITICAL, tier: TIER_1_ENFORCED }
  "P001": { enabled: true, severity: CRITICAL, tier: TIER_1_ENFORCED }
  "R001": { enabled: true, severity: MAJOR,    tier: TIER_1_ENFORCED, params: { threshold: 0.85 } }
coverage:
  test: { min: 0.80 }
  doc:  { min: 0.90 }
gate:
  fail_on_tier: TIER_1_ENFORCED
  fail_on_coverage_regression: true
```

**Exit code:** 0 on success, 1 if destination exists and `--force` not given.

---

## 6. `dbtcov scan`

Runs the full pipeline and writes reports to `--out` (default `dbtcov-out/`). Does **not** exit non-zero on findings — that's `gate`'s job. Exits non-zero only on scan failures (config errors, total render failure, unhandled exceptions).

```python
@click.command("scan")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--config", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="Path to dbtcov.yml (default: auto-discover)")
@click.option("--out", type=click.Path(path_type=Path), default=Path("dbtcov-out"),
              help="Output directory for reports")
@click.option("--format", "formats", multiple=True,
              type=click.Choice(["sarif","json","console"]),
              default=("console","sarif","json"),
              help="Report formats (repeatable)")
@click.option("--dialect", default=None, help="sqlglot dialect override")
@click.option("--render-mode", type=click.Choice(["mock","partial","dbt"]), default=None)
@click.option("--confidence-threshold", type=float, default=None)
@click.pass_context
def scan_cmd(ctx, path, config, out, formats, dialect, render_mode, confidence_threshold):
    overrides = _build_overrides(dialect, render_mode, confidence_threshold)
    result = orchestrator.scan(path, config_path=config, cli_overrides=overrides)
    _emit_reports(result, out, formats, no_color=ctx.obj["no_color"])
    _exit_on_fatal(result)   # only fatal scan errors, not findings
```

**Fatal scan errors that exit non-zero:**
- Config validation failure (Pydantic error).
- No models discovered (empty project) — `exit 2` with message.
- ≥ 90% of models failed to parse — likely config/dialect mismatch — `exit 3` with message.
- Uncaught Python exception — `exit 70` (EX_SOFTWARE convention).

Otherwise scan always exits 0, even with findings.

### 6.1 `orchestrator.scan()`

```python
def scan(
    path: Path,
    *,
    config_path: Path | None = None,
    cli_overrides: dict | None = None,
) -> ScanResult:
    """
    1. discover project root from PATH
    2. load DbtcovConfig (SPEC-02) with CLI overrides
    3. scan source files (SPEC-03) → ProjectIndex
    4. render all via Jinja (SPEC-05, ProcessPoolExecutor) → list[ParsedNode]
    5. parse all via sqlglot (SPEC-06) → mutates ParsedNode.ast
    6. build AnalysisGraph (SPEC-18)
    7. discover rules, apply overrides (SPEC-07)
    8. Engine.run(parsed_nodes) → list[Finding]
    9. compute coverage (SPEC-09a) → list[CoverageMetric]
   10. assemble ScanResult; populate RenderStats
    """
```

Pure orchestration. Single public function — reused by `scan_cmd` and `gate_cmd`.

### 6.2 Output

On `dbtcov scan .`:
- `dbtcov-out/findings.sarif`
- `dbtcov-out/findings.json`
- `dbtcov-out/coverage.json` — standalone coverage payload for quick consumption.
- Console report to stdout.

**`coverage.json`** is a convenience slice: `{ "coverage": result.coverage }`. Keeps the JSON focused; the full scan is in `findings.json`.

Creates `out` directory if missing; `--out` path can be absolute or relative.

---

## 7. `dbtcov gate`

Same pipeline as `scan`, then evaluates the gate. Exits non-zero on gate failure.

```python
@click.command("gate")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--config", ...)                           # same as scan
@click.option("--out", ...)                              # same as scan
@click.option("--format", "formats", ...)                # same as scan
@click.option("--dialect", ...)
@click.option("--render-mode", ...)
@click.option("--confidence-threshold", ...)
# gate-specific:
@click.option("--baseline", default=None,
              help="Path to baseline file, or 'git:<ref>' (e.g. 'git:main'). "
                   "Phase-1 stub: validates argument but baseline diff is NYI.")
@click.option("--fail-on-tier", type=click.Choice(["TIER_1_ENFORCED","TIER_2_WARN"]),
              default=None)
@click.option("--fail-on-new-only", is_flag=True, default=None)
@click.pass_context
def gate_cmd(ctx, path, config, out, formats, dialect, render_mode,
             confidence_threshold, baseline, fail_on_tier, fail_on_new_only):
    overrides = _build_overrides(dialect, render_mode, confidence_threshold)
    result = orchestrator.scan(path, config_path=config, cli_overrides=overrides)

    dbtcov_cfg = _loaded_config(result)
    gate_cfg = GateConfig.from_dbtcov(dbtcov_cfg)
    if fail_on_tier: gate_cfg = gate_cfg.model_copy(update={"fail_on_tier": fail_on_tier})
    if fail_on_new_only is not None:
        gate_cfg = gate_cfg.model_copy(update={"fail_on_new_only": fail_on_new_only})

    if baseline:
        click.secho("Note: --baseline is parsed but diff is not yet implemented (SPEC-19)",
                    fg="yellow", err=True)
    if gate_cfg.fail_on_new_only and not baseline:
        click.secho("Warning: fail_on_new_only=True without --baseline — no findings will count.",
                    fg="yellow", err=True)

    _emit_reports(result, out, formats, no_color=ctx.obj["no_color"], gate_config=gate_cfg)

    gate_result = evaluate(result, gate_cfg)
    _print_gate_summary(gate_result, err=True)
    sys.exit(0 if gate_result.passed else 1)
```

**Exit codes:**
| Code | Meaning |
|---|---|
| 0 | Gate passed |
| 1 | Gate failed (findings or coverage) |
| 2 | No models discovered |
| 3 | ≥90% parse failure (likely config mismatch) |
| 64 | Config validation error (EX_USAGE) |
| 70 | Internal error (EX_SOFTWARE) |

**Baseline flag** is plumbed through so CI invocations don't break when SPEC-19 lands — just prints a notice for phase 1.

---

## 8. `dbtcov rules` (ship-if-time-permits)

Lists all discovered rules with tier, severity, enabled flag. Helps users write `dbtcov.yml` confidently.

```
$ dbtcov rules
ID     SEVERITY  TIER               CATEGORY     ENABLED   DESCRIPTION
Q001   MAJOR     TIER_2_WARN        QUALITY      ✓         SELECT * in non-source model or CTE
Q002   CRITICAL  TIER_1_ENFORCED    QUALITY      ✓         Model missing primary-key test
P001   CRITICAL  TIER_1_ENFORCED    PERFORMANCE  ✓         Cross-join / cartesian product
R001   MAJOR     TIER_1_ENFORCED    REFACTOR     ✓         Near-duplicate models
```

`--format=json` for scripting.

---

## 9. `dbtcov report FORMAT` (ship-if-time-permits)

Re-emits the last scan's `findings.json` in a different format without re-running the scan. Useful for switching from console to SARIF after the fact.

```
dbtcov report sarif --input dbtcov-out/findings.json --out dbtcov-out/findings.sarif
```

---

## 10. Shared helpers

### 10.1 `_build_overrides()`

Collects non-None CLI flags into a dict that `DbtcovConfig` (SPEC-02) merges with file + defaults. None values are skipped so they don't overwrite config-file settings.

### 10.2 `_emit_reports(result, out, formats, no_color, gate_config=None)`

Instantiates reporters from the `REPORTERS` registry (SPEC-10a), calls `emit()` for each requested format. ConsoleReporter gets `gate_config` so it can preview gate status in its footer even on `scan` (informational) and `gate` (actual).

### 10.3 `_configure_logging(level)`

Structured JSON logs to stderr when `DBTCOV_LOG_JSON=1`, human-readable otherwise. Logger name `dbtcov.*` throughout the package.

### 10.4 `_print_gate_summary(gate_result, err=True)`

```
Gate: FAIL
  - 3 finding(s) at tier TIER_1_ENFORCED or higher (Q002, P001, R001)
  - doc coverage 73% < min 90%
```

Or:
```
Gate: PASS (0 findings at tier TIER_1_ENFORCED, all coverage thresholds met)
```

Printed to stderr so CI logs stay parseable (stdout can be redirected to a file).

---

## 11. Edge cases

| Case | Expected |
|---|---|
| `dbtcov scan` in a directory that's not a dbt project | Exit 64, message: "no dbt_project.yml found" |
| `dbtcov scan --config /nonexistent.yml` | Exit 64 via click (`exists=True` validator) |
| `--dialect=fake` | Passed through; SPEC-06 falls back to dialect-free parse |
| `--render-mode=dbt` but `dbt-core` extra not installed | Error at render time, exit 64 or fall back per SPEC-05 (falls back; logs notice) |
| Scan interrupted (Ctrl-C) | `KeyboardInterrupt` caught; partial output suppressed; exit 130 |
| Output dir exists with old reports | Overwritten (no `--force` required for output) |
| `--format=console` only, `--out=...` ignored for console | Accept; document that console always goes to stdout |
| `gate` with no gate config and no findings | PASS, exit 0 |
| `gate` with `fail_on_new_only` but no `--baseline` | PASS always but print warning to stderr |
| Multiple `--format` flags | Each emitted; duplicates deduped |
| Windows path as PATH argument | Works; paths normalized via `pathlib.Path` |
| `DBTCOV_LOG_JSON=1` set | Structured logs to stderr |
| Running inside a worktree subdirectory (not project root) | `project_discovery` walks up until `dbt_project.yml` is found |
| `--log-level=DEBUG` | Verbose render/parse diagnostics on stderr |

---

## 12. Tests (`tests/unit/cli/`, `tests/integration/cli/`)

### 12.1 Unit (`tests/unit/cli/`)
- `_build_overrides` skips Nones.
- `_print_gate_summary` output matches golden for pass and fail cases.
- `init_cmd` on existing file without `--force` exits 1.
- `init_cmd` writes template byte-identically.
- `scan_cmd` with missing PATH errors via click before invoking orchestrator.
- `gate_cmd` with `fail_on_new_only` but no `--baseline` prints warning (capture stderr).

### 12.2 Integration (`tests/integration/cli/`)
Uses `click.testing.CliRunner` and the fixture project from SPEC-13.
- `dbtcov init` in temp dir → file exists, parses as valid `dbtcov.yml`.
- `dbtcov scan fixture/` → exit 0, `findings.sarif` present, valid SARIF.
- `dbtcov gate fixture/` on clean fixture → exit 0.
- `dbtcov gate fixture/` on broken fixture (introduce a Q002 violation) → exit 1, stderr contains "Gate: FAIL".
- `dbtcov scan --format=json fixture/` → only `findings.json` emitted.
- `dbtcov scan fixture/ --render-mode=dbt` without dbt-core extra installed → exits 0 with fallback notice (render falls back to partial/mock per SPEC-05).
- `dbtcov --version` prints the package version.
- `dbtcov scan nonexistent/` → click error, exit != 0.

**Coverage target:** 90% across `cli/`.

---

## 13. Acceptance criteria

- [ ] `pip install -e .` puts `dbtcov` on PATH and `dbtcov --help` lists `init`, `scan`, `gate`.
- [ ] `dbtcov init` in an empty directory creates a valid `dbtcov.yml` (parses via SPEC-02 loader).
- [ ] `dbtcov scan examples/basic_project/` produces the three output files and a tiered console report.
- [ ] `dbtcov gate examples/basic_project/` exits 0 on the clean fixture and 1 when a Q002 violation is introduced.
- [ ] Exit code table (§7) enforced in integration tests.
- [ ] `--no-color` disables all ANSI output; `NO_COLOR` env var honored by `rich`.
- [ ] `ruff`, `mypy --strict` clean.
- [ ] ≥90% coverage on `tests/*/cli/`.

---

## 14. Open questions

- Should `scan` default to writing reports to a timestamped subdir under `dbtcov-out/` (preserving history)? **Proposal:** no — overwrite by default to keep CI ergonomics simple; users can `--out=dbtcov-out/$(date +%F)` if they want history.
- Should `gate` and `scan` be the same command with a `--gate` flag? **Proposal:** no — keeping them separate makes CI configs explicit (`gate` signals intent to fail the pipeline) and matches prior art (SonarQube's `sonar-scanner` vs. webhook gate).
- Progress bar for long scans? **Proposal:** phase 2 — `rich.progress` integration needs careful handling with ProcessPoolExecutor. Phase 1 logs per-phase start/end at INFO.
- Should `--only <path>` single-file mode ship in phase 1? **Proposal:** phase 2 alongside `--changed-only` — both need cache infrastructure from SPEC-19 for meaningful speed.
