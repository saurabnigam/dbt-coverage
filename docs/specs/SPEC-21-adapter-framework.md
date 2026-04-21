# SPEC-21 — External Tool Adapter Framework

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-01 (domain model), SPEC-02 (config), SPEC-07 (rule engine), SPEC-12 (CLI)
**Blocks:** SPEC-22 (weighted coverage consumes adapter-produced `TestResult`s), SPEC-23 (dbt-test adapter), SPEC-24 (sqlfluff adapter), all future adapters

---

## 1. Purpose

Make dbtcov a **unified signal collector** for the analytics-engineering toolchain, the same way JaCoCo aggregates coverage data from any JVM test runner or Sonar aggregates issues from any plugin. Every external signal — dbt's own test runs, sqlfluff, dbt-coverage, dbt-project-evaluator, Elementary, dbt-checkpoint, future tools we don't know about — ships as an `Adapter` implementing a small Protocol. The framework provides:

- **Discovery** — built-in adapters + Python entry-point `dbt_coverage.adapters` for third parties.
- **Execution modes** — `read` (consume an existing report file), `run` (invoke the tool via subprocess), `auto` (read-if-present, else run).
- **Normalisation** — adapters emit `Finding`s, `CoverageMetric`s, and `TestResult`s in the shared domain model. Consumers never touch tool-specific JSON schemas.
- **Provenance** — every adapter invocation recorded on `ScanResult.adapter_invocations` (tool + version + args + output path + status).
- **Dedup** — identical findings emitted by two adapters merge via fingerprint; the `origins[]` list preserves attribution.
- **Failure isolation** — any adapter may fail without aborting the scan; its failure becomes an `INTERNAL` finding.

This is the backbone for SPECs 22–24 and any future external signal.

---

## 2. Non-goals

- Not an IPC framework. Adapters run in-process; `run` mode uses `subprocess`.
- Not a generic plugin host. The only extensibility surface is: produce `Finding | CoverageMetric | TestResult`. Adapters cannot mutate config, rule registry, or scan order.
- Not a caching layer. A later spec (`SPEC-25-adapter-cache`) can add fingerprint-keyed cache; out of scope here.
- Not a language-agnostic protocol (no JSON-RPC, no stdio protocol). Pure Python Protocol.

---

## 3. Module layout

```
src/dbt_coverage/adapters/
  __init__.py              # Registry + discover_adapters()
  base.py                  # Adapter Protocol, AdapterResult, AdapterInvocation, AdapterMode
  scheduler.py             # run_adapters(config, ctx) -> list[AdapterResult]
  dedup.py                 # merge findings across adapters by fingerprint
  errors.py                # AdapterError, AdapterTimeoutError, AdapterNotRunnableError
```

Third-party adapters live in their own packages and register via entry points (§6.2).

---

## 4. Data model

### 4.1 Enum: `AdapterMode`

```python
from enum import StrEnum

class AdapterMode(StrEnum):
    READ = "read"
    RUN  = "run"
    AUTO = "auto"      # read if output present, else run
```

### 4.2 `TestResult` — new shared type (also used by SPEC-22/23)

Lives in `src/dbt_coverage/core/test_result.py`:

```python
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class TestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class TestResult(BaseModel):
    """
    A single test outcome, tool-agnostic.
    Emitted by dbt-test adapter, dbt-expectations runs, custom test runners.
    Consumed by SPEC-22 weighted coverage.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    test_name: str                              # e.g. "not_null_dim_customers_id"
    test_kind: str                              # "not_null" | "unique" | "accepted_values" |
                                                # "relationships" | "unique_combination_of_columns" |
                                                # "singular" | "unit_test" | "dbt_expectations.*" | ...
    model_unique_id: str | None = None          # dbt unique_id of the model under test (None for source/seed tests)
    column_name: str | None = None
    status: TestStatus = TestStatus.UNKNOWN
    file_path: Path | None = None               # relative if available
    origin: str                                 # adapter name, e.g. "dbt-test"
    raw_kind: str | None = None                 # tool-specific identifier (macro name, etc.)
```

### 4.3 `AdapterInvocation` — provenance record

```python
class AdapterInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str                                # registered name, e.g. "sqlfluff"
    mode: AdapterMode
    tool_version: str | None = None             # "3.0.7"
    argv: list[str] = Field(default_factory=list)   # for RUN mode
    report_path: Path | None = None             # output file consumed
    started_at_ms: int = 0                      # epoch ms
    duration_ms: int = 0
    status: str                                 # "ok" | "not_runnable" | "read_failed" | "run_failed" | "timeout"
    message: str | None = None                  # short human-readable
```

### 4.4 `AdapterResult` — what each adapter returns

```python
class AdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    adapter: str
    findings: list["Finding"] = Field(default_factory=list)
    coverage: list["CoverageMetric"] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    invocation: AdapterInvocation
```

Adapters never produce `ParsedNode`s (that's the core scanner's job) and never mutate `ScanResult` directly.

### 4.5 Extensions to `ScanResult`

```python
adapter_invocations: list[AdapterInvocation] = Field(default_factory=list)
test_results: list[TestResult] = Field(default_factory=list)
# findings list gains an optional `origins: list[str]` field (see dedup §7)
```

`Finding` becomes:

```python
class Finding(BaseModel):
    # ... existing fields ...
    origins: list[str] = Field(default_factory=list)   # ["engine", "sqlfluff", "dbt-project-evaluator", ...]
```

`origins` is excluded from fingerprint computation (§7).

---

## 5. `Adapter` Protocol

```python
from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    name: str                                        # registry key, e.g. "sqlfluff"
    display_name: str                                # human label, e.g. "SQLFluff"
    output_kinds: tuple[str, ...]                    # informational: ("findings",), ("test_results",), ("findings", "coverage")
    default_report_path: Path | None                 # e.g. Path("target/run_results.json")
    default_mode: "AdapterMode"                      # usually AUTO

    # -- discovery ---------------------------------------------------------
    def discover(self, project_root: Path, cfg: "AdapterConfig") -> Path | None:
        """Return the path of an existing report file, if any, else None."""
        ...

    def is_runnable(self) -> bool:
        """True if the tool binary is available (e.g. `which sqlfluff`)."""
        ...

    # -- execution --------------------------------------------------------
    def run(self, project_root: Path, cfg: "AdapterConfig") -> Path:
        """Invoke the external tool, produce a report file, return its path.
        May raise AdapterNotRunnableError, subprocess.TimeoutExpired, etc.
        Runner catches and converts to an InvocationRecord."""
        ...

    def read(self, report_path: Path, cfg: "AdapterConfig") -> AdapterResult:
        """Parse a report file and produce normalised results. Never shells out."""
        ...

    def tool_version(self) -> str | None:
        """Best-effort tool version string for provenance. None if unknown."""
        ...
```

**Minimum viable adapter:** implement `name`, `output_kinds`, `discover`, `read`. `run`, `is_runnable`, `tool_version` can no-op (`is_runnable` returns False, `run` raises `AdapterNotRunnableError`) — the adapter is then read-only. Most built-ins implement all five.

---

## 6. Registry & discovery

### 6.1 Public API (`adapters/__init__.py`)

```python
def register_adapter(adapter: Adapter) -> None: ...
def get_adapter(name: str) -> Adapter: ...
def list_adapters() -> list[Adapter]: ...
def discover_adapters() -> None:
    """Load built-ins + entry-point `dbt_coverage.adapters`. Idempotent."""
```

Built-ins registered in `adapters/__init__.py`:

```python
_BUILTIN = [
    "dbt_coverage.adapters.dbt_test:DbtTestAdapter",       # SPEC-23
    "dbt_coverage.adapters.sqlfluff:SqlfluffAdapter",       # SPEC-24
]
```

### 6.2 Entry points

Third-party `pyproject.toml`:

```toml
[project.entry-points."dbt_coverage.adapters"]
elementary = "my_pkg.elementary_adapter:ElementaryAdapter"
```

`discover_adapters()` uses `importlib.metadata.entry_points(group="dbt_coverage.adapters")`. Duplicate names → last registered wins, with a DEBUG log. Adapter import failure → WARNING log + one `INTERNAL` finding with `rule_id="ADAPTER_LOAD_FAIL"`, scan continues.

### 6.3 Config schema

```yaml
adapters:
  dbt-test:
    enabled: true
    mode: auto                 # read | run | auto
    report: target/run_results.json
    manifest: target/manifest.json
    timeout_seconds: 60
    argv: []                   # extra args for run mode
    params: {}                 # adapter-specific
  sqlfluff:
    enabled: true
    mode: read
    report: sqlfluff-report.json
    timeout_seconds: 30
    params:
      severity_map: {...}
```

Pydantic model:

```python
class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # allow adapter-specific params
    enabled: bool = True
    mode: AdapterMode = AdapterMode.AUTO
    report: Path | None = None
    timeout_seconds: int = Field(default=60, ge=1)
    argv: list[str] = []
    params: dict[str, object] = Field(default_factory=dict)
```

Top-level `DbtcovConfig.adapters: dict[str, AdapterConfig]` (indexed by adapter name).

---

## 7. Dedup via fingerprint + `origins`

Two adapters may flag the same issue (SPEC-07 Q002 ≈ SQLFluff `L010`). The dedup step:

```python
def merge_findings(batches: list[list[Finding]]) -> list[Finding]:
    by_fp: dict[str, Finding] = {}
    for batch in batches:
        for f in batch:
            if f.fingerprint in by_fp:
                merged = by_fp[f.fingerprint]
                merged_origins = list(dict.fromkeys(merged.origins + f.origins))
                by_fp[f.fingerprint] = merged.model_copy(update={"origins": merged_origins})
            else:
                by_fp[f.fingerprint] = f
    return list(by_fp.values())
```

Fingerprint is computed from `(rule_id, file_path, code_context)` (SPEC-01 §4.7) — `origins` is **not** part of fingerprint input. This means two adapters emitting the "same" finding must agree on `rule_id`; they rarely do (sqlfluff uses `SQLF.L010`, internal engine uses `Q002`), so by default they co-exist. Users can opt into aggressive merging via `adapters.dedup.rule_id_aliases: { "Q002": ["SQLF.L010"] }` — normalises to the first listed id at merge time.

Engine-produced findings are tagged `origins=["engine"]` centrally in the scheduler. Adapter-produced findings must tag themselves with `origins=[self.name]` (validated in the scheduler).

---

## 8. Scheduler (`adapters/scheduler.py`)

```python
def run_adapters(
    project_root: Path,
    cfg: "DbtcovConfig",
) -> tuple[list[AdapterResult], list[AdapterInvocation]]:
    results: list[AdapterResult] = []
    invocations: list[AdapterInvocation] = []

    for name, adapter in list_adapters_iter():
        acfg = cfg.adapters.get(name) or AdapterConfig()
        if not acfg.enabled:
            continue
        inv = AdapterInvocation(adapter=name, mode=acfg.mode, status="ok")
        started = time.time()
        try:
            report = _resolve_report(adapter, project_root, acfg)
            if report is None:
                raise AdapterNotRunnableError(f"{name}: no report available (mode={acfg.mode})")
            ar = adapter.read(report, acfg)
            _stamp_origins(ar, name)
            inv.report_path = report
            inv.tool_version = adapter.tool_version()
            results.append(ar)
        except AdapterNotRunnableError as e:
            inv.status, inv.message = "not_runnable", str(e)
        except subprocess.TimeoutExpired as e:
            inv.status, inv.message = "timeout", str(e)
        except Exception as e:
            inv.status, inv.message = "run_failed" if acfg.mode == AdapterMode.RUN else "read_failed", str(e)
            _emit_internal_finding(results, name, e)       # ADAPTER_FAILED finding, tier=TIER_2_WARN
        finally:
            inv.duration_ms = int((time.time() - started) * 1000)
            invocations.append(inv)

    return results, invocations


def _resolve_report(adapter, project_root, acfg) -> Path | None:
    report = acfg.report or adapter.default_report_path
    if acfg.mode == AdapterMode.READ:
        if report and (project_root / report).exists():
            return project_root / report
        raise AdapterNotRunnableError(f"report not found at {report}")
    if acfg.mode == AdapterMode.RUN:
        if not adapter.is_runnable():
            raise AdapterNotRunnableError(f"{adapter.name} binary not found")
        return adapter.run(project_root, acfg)
    # AUTO
    if report and (project_root / report).exists():
        return project_root / report
    if adapter.is_runnable():
        return adapter.run(project_root, acfg)
    return None  # adapter skipped silently
```

`_emit_internal_finding` appends to `results[-1].findings` (or creates a new `AdapterResult`) a `Finding(rule_id="ADAPTER_FAILED", severity=MINOR, tier=TIER_2_WARN, category=GOVERNANCE, ...)` — visible but never blocking by default.

---

## 9. CLI integration (§ extends SPEC-12)

```
dbtcov scan [--adapter NAME]... [--no-adapter NAME]...
            [--adapter-report NAME=PATH]...
            [--adapter-mode NAME=MODE]...
            [--list-adapters]
```

- `--adapter foo` → forces `adapters.foo.enabled=true`; repeats allowed.
- `--no-adapter foo` → forces `adapters.foo.enabled=false`.
- `--adapter-report sqlfluff=./ci/sqlfluff.json` → overrides `adapters.sqlfluff.report`.
- `--adapter-mode dbt-test=read` → overrides `mode`.
- `--list-adapters` → prints discovered adapters and exits zero (format: `name  | default_mode | runnable | report_default`).

Invocations table printed in console summary:

```
Adapters
  dbt-test   read   target/run_results.json   ok   42ms
  sqlfluff   read   sqlfluff-report.json      ok    8ms
```

---

## 10. Failure isolation

| Condition | Behavior |
|---|---|
| Adapter import error (entry point) | Logged; `ADAPTER_LOAD_FAIL` `INTERNAL` finding; scan continues. |
| Adapter disabled in config | Silent skip; no invocation record. |
| Adapter enabled but report missing and not runnable | `status=not_runnable` invocation; no finding unless `strict_adapters=true` in config (then `ADAPTER_NOT_RUNNABLE` at tier-2). |
| Adapter `read()` raises | `status=read_failed`; `ADAPTER_FAILED` finding; other adapters still run. |
| Adapter `run()` raises / times out | `status=run_failed` or `timeout`; `ADAPTER_FAILED` finding. |
| Adapter returns findings without `origins` set | Scheduler overwrites with `[adapter.name]`. Emits DEBUG log. |
| Two adapters claim the same `name` via entry points | Last-registered wins; DEBUG log. (`--list-adapters` shows the effective one.) |
| Adapter tries to emit a `Finding` with non-existent file_path | Validated by `Finding` model; adapter error → fails isolation as above. |

---

## 11. Tests (`tests/unit/adapters/`)

### 11.1 Protocol / registry
- A class missing `read` fails `isinstance(obj, Adapter)` runtime check.
- `register_adapter` then `get_adapter` round-trips.
- `discover_adapters` picks up built-ins; idempotent across calls.
- Duplicate name → last-wins, DEBUG log recorded.

### 11.2 Scheduler
- Adapter returns findings → scheduler stamps `origins=[name]`.
- Adapter disabled → not invoked; no invocation record.
- `mode=read` + missing report → `AdapterNotRunnableError` → invocation status `not_runnable`.
- `mode=run` + `is_runnable()==False` → status `not_runnable`.
- `mode=auto` + report exists → `read()` path exercised.
- `mode=auto` + no report, runnable → `run()` path exercised with a fake subprocess.
- `read()` raises → `ADAPTER_FAILED` finding appended, scan continues.
- Timeout → `status=timeout`, finding emitted, no hang.

### 11.3 Dedup
- Two findings with identical fingerprint but different origins → merged; `origins=["a", "b"]`.
- Two findings with different fingerprints → both kept.
- `rule_id_aliases` config → matching findings collapse under canonical id.

### 11.4 CLI
- `--list-adapters` prints the built-in two (dbt-test, sqlfluff) and exits zero.
- `--adapter-report` override propagates into `AdapterConfig.report`.
- `--no-adapter sqlfluff` suppresses it even if config enables it.

---

## 12. Acceptance criteria

- [ ] `src/dbt_coverage/adapters/{__init__,base,scheduler,dedup,errors}.py` per §3 / §5 / §6 / §7 / §8.
- [ ] `TestResult`, `TestStatus`, `AdapterInvocation`, `AdapterResult`, `AdapterMode` publicly importable from `dbt_coverage.adapters`.
- [ ] `ScanResult` has `adapter_invocations` and `test_results` populated on every scan.
- [ ] Built-in adapters SPEC-23 + SPEC-24 register via the registry, not hand-wired in the orchestrator.
- [ ] CLI flags §9 implemented; `--list-adapters` prints the table.
- [ ] Any adapter failure produces a single `ADAPTER_FAILED` `INTERNAL` finding and does not abort the scan.
- [ ] `ruff` + `mypy --strict` clean; tests ≥90% line coverage on the module.

---

## 13. Open questions

- Should adapters be ordered deterministically for reproducible merges? *Proposal: alphabetical by `name`. Entry-point packages can't depend on registration order for correctness; dedup is symmetric anyway.*
- Should `test_results` be a stream (iterator) rather than a list, to support large dbt projects? *Proposal: list in v1 (dbt projects with >100k test results are vanishingly rare). Revisit on user report.*
- Should the scheduler run adapters concurrently (ThreadPoolExecutor)? *Proposal: sequential in v1 — each adapter does subprocess-bound work; threading adds test complexity without a clear gain on typical CI runners. Revisit.*
