# SPEC-24 — Adapter: SQLFluff

**Status:** draft (awaiting sign-off)
**Depends on:** SPEC-01, SPEC-21 (Adapter framework)
**Blocks:** none

---

## 1. Purpose

Second concrete built-in `Adapter`. Consumes SQLFluff lint output (JSON) and emits normalised `Finding`s under the `SQLF.*` rule-id namespace. Supports both:

- **Read mode** (default, CI-friendly): user runs `sqlfluff lint --format json` in CI, pipes to a file, dbtcov reads it. Zero runtime dep on sqlfluff.
- **Run mode**: dbtcov invokes `sqlfluff lint --format json` via `subprocess` for local dev and one-shot scans where installing sqlfluff alongside dbtcov is acceptable.

This is the canonical example of how third-party linters plug into dbtcov under SPEC-21. The adapter is intentionally small so it doubles as a template for future adapters (`dbt-project-evaluator`, Elementary, custom).

---

## 2. Non-goals

- Not a SQLFluff config loader. SQLFluff's own `.sqlfluff`/`.sqlfluff.toml` is picked up by `sqlfluff` itself; this adapter only transforms its output.
- Not a severity-inferring tool. Severity follows SQLFluff's own `warning`/`error` or our configured `severity_map`.
- Not a fixer. We do not shell out `sqlfluff fix`; that would mutate user code. A separate future adapter (`SPEC-25-adapter-sqlfluff-fix`) can do that.
- Not a parser of SQLFluff's human output format. JSON only.

---

## 3. Supported SQLFluff versions

| sqlfluff version | JSON schema | Status |
|---|---|---|
| 2.x | `violations[]` with `line_no`, `line_pos`, `code`, `description`, `name` | Supported |
| 3.x | Same shape plus `warning` flag, some renamed fields (`start_line_no` / `start_line_pos`) | Supported |
| 1.x | Different field names | UNSUPPORTED (user gets a clear error) |

Version detection: `sqlfluff --version` for run mode; for read mode, we sniff the first violation record for the `start_line_no` key (3.x) vs `line_no` (2.x). Absent both, we default to 2.x compat. Graceful degradation — unknown fields are ignored, known ones read best-effort.

---

## 4. Module layout

```
src/dbt_coverage/adapters/sqlfluff/
  __init__.py
  adapter.py             # SqlfluffAdapter class
  parser.py              # parse_sqlfluff_json(text | path) -> list[SqlfluffViolation]
  mapper.py              # violation -> Finding
```

---

## 5. Adapter class

```python
class SqlfluffAdapter:
    name: str = "sqlfluff"
    display_name: str = "SQLFluff"
    output_kinds: tuple[str, ...] = ("findings",)
    default_report_path: Path | None = Path("sqlfluff-report.json")
    default_mode = AdapterMode.AUTO

    def discover(self, project_root: Path, cfg: AdapterConfig) -> Path | None:
        p = (cfg.report or self.default_report_path)
        p = p if p.is_absolute() else project_root / p
        return p if p.exists() else None

    def is_runnable(self) -> bool:
        return shutil.which("sqlfluff") is not None

    def tool_version(self) -> str | None:
        if not self.is_runnable():
            return None
        try:
            out = subprocess.check_output(["sqlfluff", "--version"],
                                          text=True, timeout=5)
            return out.strip().split()[-1]            # "3.0.7"
        except Exception:
            return None

    def run(self, project_root: Path, cfg: AdapterConfig) -> Path:
        if not self.is_runnable():
            raise AdapterNotRunnableError("sqlfluff binary not on PATH")

        out_path = project_root / (cfg.report or self.default_report_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        argv = ["sqlfluff", "lint", "--format", "json"]
        # User-supplied extras: dialect, paths, config, etc.
        argv.extend(cfg.argv or ["."])

        timeout = cfg.timeout_seconds
        proc = subprocess.run(
            argv,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,            # sqlfluff exits non-zero if issues found; that's OK
        )
        # sqlfluff writes JSON to stdout.
        out_path.write_text(proc.stdout or "[]", encoding="utf-8")
        return out_path

    def read(self, report_path: Path, cfg: AdapterConfig) -> AdapterResult:
        text = report_path.read_text(encoding="utf-8")
        violations = parse_sqlfluff_json(text)
        findings = []
        severity_map = _severity_map(cfg)
        for v in violations:
            findings.append(violation_to_finding(v, severity_map))
        return AdapterResult(
            adapter=self.name,
            findings=findings,
            invocation=AdapterInvocation(
                adapter=self.name, mode=cfg.mode,
                tool_version=self.tool_version(),
                report_path=report_path, status="ok",
            ),
        )
```

---

## 6. JSON parsing (`parser.py`)

### 6.1 Input shape (sqlfluff 2.x / 3.x)

```json
[
  {
    "filepath": "models/marts/dim_customers.sql",
    "violations": [
      {
        "line_no": 12,
        "line_pos": 3,
        "start_line_no": 12,
        "start_line_pos": 3,
        "code": "L010",
        "description": "Keywords must be consistently lower case.",
        "name": "capitalisation.keywords",
        "warning": false
      }
    ]
  }
]
```

### 6.2 Internal model

```python
@dataclass(frozen=True)
class SqlfluffViolation:
    file_path: Path             # relative to project_root if possible
    line: int                   # 1-indexed
    column: int                 # 1-indexed
    code: str                   # "L010"
    name: str                   # "capitalisation.keywords"
    description: str
    is_warning: bool            # sqlfluff 3.x `warning` flag; False for 2.x
```

### 6.3 Algorithm

```python
def parse_sqlfluff_json(text: str) -> list[SqlfluffViolation]:
    if not text.strip():
        return []
    data = json.loads(text)
    # sqlfluff emits a top-level list; defensively accept a dict with "files" too.
    if isinstance(data, dict) and "files" in data:
        data = data["files"]
    out: list[SqlfluffViolation] = []
    for f in data:
        fp = Path(f.get("filepath") or "")
        for v in f.get("violations") or []:
            line = int(v.get("start_line_no") or v.get("line_no") or 1)
            col  = int(v.get("start_line_pos") or v.get("line_pos") or 1)
            out.append(SqlfluffViolation(
                file_path=fp,
                line=max(line, 1),
                column=max(col, 1),
                code=str(v.get("code") or "").strip() or "UNKNOWN",
                name=str(v.get("name") or "").strip(),
                description=str(v.get("description") or "").strip(),
                is_warning=bool(v.get("warning", False)),
            ))
    return out
```

---

## 7. Mapping to `Finding`

```python
_DEFAULT_SEVERITY_MAP = {
    # code-prefix -> Severity
    "L01": Severity.MINOR,      # capitalisation
    "L02": Severity.MINOR,      # whitespace
    "L03": Severity.MINOR,      # alias
    "L04": Severity.MAJOR,      # commas
    "L05": Severity.MINOR,
    "L06": Severity.MINOR,
    "L07": Severity.MAJOR,
    "L08": Severity.MINOR,
    "L09": Severity.MINOR,
    # Fallback bucket
    "__default__": Severity.MINOR,
}


def violation_to_finding(v: SqlfluffViolation, severity_map: dict[str, Severity]) -> Finding:
    prefix3 = v.code[:3] if v.code else ""
    severity = severity_map.get(v.code) or severity_map.get(prefix3) or severity_map["__default__"]
    if v.is_warning and severity in (Severity.MAJOR, Severity.CRITICAL):
        severity = Severity.MINOR
    rule_id = f"SQLF.{v.code}"
    fp = compute_fingerprint(
        rule_id=rule_id,
        file_path=v.file_path,
        code_context=f"SQLF:{v.code}:{v.name}:L{v.line}",
    )
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=Tier.TIER_2_WARN,
        confidence=0.9,
        message=f"[{v.code} {v.name}] {v.description}",
        file_path=v.file_path,
        line=v.line,
        column=v.column,
        fingerprint=fp,
        origins=["sqlfluff"],
    )
```

### 7.1 Severity override config

```yaml
adapters:
  sqlfluff:
    params:
      severity_map:
        L010: CRITICAL          # per-code override
        L07:  BLOCKER           # per-prefix override
        __default__: INFO       # fallback
```

Override precedence: exact code (`L010`) > 3-char prefix (`L01`) > `__default__`. If sqlfluff flags a violation as `warning=true`, we clamp severity down to `MINOR` unless overridden to `INFO`.

### 7.2 Tier

Always `TIER_2_WARN` in v1. Users can still promote via the rule-engine's tier-override mechanism on specific `rule_id`s (SPEC-07 §... `rules.SQLF.L010.tier: TIER_1_ENFORCED`) — zero new code needed because the engine's override layer is rule-id-keyed.

### 7.3 File path normalisation

SQLFluff may emit absolute paths depending on how it was invoked. Adapter normalisation:

```python
fp = Path(raw)
if fp.is_absolute():
    try:
        fp = fp.relative_to(project_root)
    except ValueError:
        # outside project_root — drop the violation (with a DEBUG log).
        continue
```

---

## 8. Config surface

```yaml
adapters:
  sqlfluff:
    enabled: true
    mode: auto                     # read | run | auto
    report: sqlfluff-report.json
    timeout_seconds: 60
    argv:                          # extra args when mode=run
      - "--dialect"
      - "snowflake"
      - "models/"
    params:
      severity_map:
        __default__: MINOR
      rule_id_aliases:             # handled by SPEC-21 dedup, not this adapter
        Q002: ["SQLF.L010"]
```

`rule_id_aliases` is documented here but *consumed* by the SPEC-21 dedup step.

---

## 9. Failure modes

| Case | Behavior |
|---|---|
| Report file missing, `mode=read` | `discover()` returns None → scheduler marks `not_runnable`. |
| Report file present but empty `[]` | Zero findings emitted; invocation ok. |
| Report file is `{}` or not a list and not `{"files": ...}` | Treated as empty with a WARNING log. |
| Report file truncated JSON | `json.JSONDecodeError` → scheduler isolates → `ADAPTER_FAILED` finding. |
| `mode=run` but binary missing | `AdapterNotRunnableError`; scheduler marks `not_runnable`. |
| `mode=run` returns non-zero (= violations found) | Expected; we use `check=False`. `read()` proceeds on the output. |
| `mode=run` exceeds timeout | `subprocess.TimeoutExpired` → scheduler marks `timeout`; no findings. |
| Violation with `line_no=0` or missing | Clamped to 1 (satisfies `Finding.line >= 1`). |
| Violation with `code=""` | `rule_id=SQLF.UNKNOWN`; still emitted. |
| File path outside project root | Violation dropped with DEBUG log. |
| SQLFluff 1.x output shape | Missing `filepath` / `violations` keys → zero findings; log WARNING `"sqlfluff 1.x output not supported"`. |
| Duplicate violations (same file, line, column, code) | Deduped by exact `(file, line, column, code)` tuple before mapping. |

---

## 10. Tests (`tests/unit/adapters/sqlfluff/`)

### 10.1 `test_parser.py`
- sqlfluff 2.x fixture → N violations parsed with correct fields.
- sqlfluff 3.x fixture (`start_line_no`) → parsed.
- Empty list `[]` → zero violations.
- `{"files": [...]}` wrapper → parsed.
- Truncated JSON → `json.JSONDecodeError`.
- Violation with `warning: true` → `is_warning=True`.
- Duplicate violation lines → deduped.

### 10.2 `test_mapper.py`
- `L010` with default map → severity MINOR, category QUALITY, rule_id `"SQLF.L010"`, tier TIER_2_WARN, confidence 0.9.
- `L041` with prefix-level override `L04: MAJOR` → severity MAJOR.
- Override `L010: CRITICAL` + `warning: true` → clamped to MINOR unless override is INFO or MINOR (documented).
- Fingerprint stable across line_no changes only when code and name match? → fingerprint uses the line in `code_context`, so line change *does* churn. Test asserts this is the behaviour.
- Violation with absolute path outside project_root → no finding.

### 10.3 `test_adapter.py` (unit, with subprocess mocked)
- `discover()` with existing default report → returns Path.
- `discover()` with missing report → None.
- `is_runnable()` when `which("sqlfluff")` returns None → False.
- `run()` with mocked subprocess returning `stdout='[]'` → writes empty JSON, returns path.
- `run()` with mocked subprocess returning timeout → `TimeoutExpired` propagates.
- `read()` on a real fixture produces findings with `origins=["sqlfluff"]`.

### 10.4 Fixtures
- `tests/fixtures/sqlfluff/2x_report.json` — 3 violations across 2 files.
- `tests/fixtures/sqlfluff/3x_report.json` — includes `start_line_no` and `warning: true`.
- `tests/fixtures/sqlfluff/empty.json` — `"[]"`.
- `tests/fixtures/sqlfluff/corrupt.json` — truncated.

---

## 11. Acceptance criteria

- [ ] `SqlfluffAdapter` registered via built-in list under `"sqlfluff"`.
- [ ] All tests in §10 pass; ≥90% coverage on the adapter + parser + mapper.
- [ ] `read` mode works without sqlfluff installed (only needs the report file).
- [ ] `run` mode invokes the binary with correct argv and writes captured stdout to the report path.
- [ ] File paths in emitted Findings are always relative to `project_root`.
- [ ] `ruff` + `mypy --strict` clean.
- [ ] End-to-end: running `dbtcov scan --adapter sqlfluff --adapter-report sqlfluff=report.json` against a fixture produces expected `SQLF.*` findings in the final `ScanResult`.

---

## 12. Open questions

- Should we auto-rewrite `SQLF.L010` → `Q002` when the dedup alias table lists them? *Proposal: no, preserve both rule_ids. Dedup merges `origins`, not rule_ids, unless the user opts in via `rule_id_aliases` (SPEC-21 §7).*
- Should we emit a coverage metric (`sqlfluff = (files clean) / (files scanned)`)? *Proposal: no — that's derivable from findings, and adding it here couples the adapter to a coverage concern.*
- Should `run` mode stream stdout to the report file rather than read-all-then-write? *Proposal: yes once we have `SPEC-25-adapter-streaming`. For v1, simple capture is fine — sqlfluff output on a typical project is < 5 MB.*
