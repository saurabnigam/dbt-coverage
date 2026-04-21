# SPEC-10a — Reporters: SARIF + JSON + Console

**Status:** draft
**Depends on:** SPEC-01, SPEC-09a
**Blocks:** SPEC-11, SPEC-12

---

## 1. Purpose

Emit `ScanResult` in three formats for the MVP:
- **SARIF 2.1.0** — machine-readable for GitHub Code Scanning, JetBrains, VS Code, any SARIF-aware UI.
- **JSON** — canonical `ScanResult.model_dump_json()`; the round-trip contract from SPEC-01.
- **Console** — human-readable `rich` tree, tiered, with coverage summary and gate preview.

Sonar Generic Issue is deferred to SPEC-10b (phase 2). It's a small delta on the JSON reporter.

---

## 2. Non-goals

- No HTML/dashboard reporter (phase 3 web dashboard).
- No CSV / markdown report.
- No Sonar Generic Issue (phase 2).
- No per-rule detail pages (console shows list, not drill-down).
- No stats on rule execution time (observability spec, later).

---

## 3. Module layout

```
src/dbt_coverage/reporters/
  __init__.py
  base.py                   # Reporter Protocol
  sarif.py                  # SARIFReporter
  json_.py                  # JSONReporter
  console.py                # ConsoleReporter (uses rich)
  _shared.py                # finding grouping helpers, severity → style maps
```

---

## 4. API Surface

### 4.1 `base.py`

```python
from pathlib import Path
from typing import Protocol
from dbt_coverage.core import ScanResult

class Reporter(Protocol):
    name: str                          # "sarif", "json", "console"
    default_filename: str | None       # e.g. "findings.sarif"; None for stdout-only (console)

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        """
        Writes the report. If `out` is None, falls back to stdout (console) or
        `out_dir / default_filename` (file reporters; CLI passes the dir).
        """
```

### 4.2 `json_.py`

```python
class JSONReporter:
    name = "json"
    default_filename = "findings.json"

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        payload = result.model_dump_json(indent=2, by_alias=False)
        if out is None:
            sys.stdout.write(payload)
            return
        out.write_text(payload, encoding="utf-8")
```

**Contract:** the JSON output is SPEC-01's canonical `ScanResult` form. A consumer can round-trip it: `ScanResult.model_validate_json(Path(out).read_text())`.

**Path handling:** `file_path` fields in Findings are relative to `project_root` (SPEC-01 invariant). JSON preserves them as-is. Absolute paths would break cross-machine comparison and portability.

### 4.3 `sarif.py`

```python
class SARIFReporter:
    name = "sarif"
    default_filename = "findings.sarif"

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        doc = self._build_sarif(result)
        text = json.dumps(doc, indent=2)
        ...

    def _build_sarif(self, result: ScanResult) -> dict:
        """
        Builds a SARIF 2.1.0 log with one run. Structure:
          {
            "version": "2.1.0",
            "$schema": "https://.../sarif-schema-2.1.0.json",
            "runs": [{
              "tool": { "driver": { "name": "dbtcov", "version": <pkg-version>, "rules": [...] }},
              "results": [...],
              "invocations": [{ "executionSuccessful": true, "workingDirectory": {"uri": ...}}],
              "properties": {
                "coverage": [...],           # dbtcov-specific
                "renderStats": {...},
              }
            }]
          }
        """
```

**Rules block:** one entry per rule_id referenced in `result.findings` plus any `INTERNAL_CRASH`. Fields:
```json
{
  "id": "Q001",
  "name": "SelectStar",
  "shortDescription": {"text": "SELECT * in non-source model"},
  "fullDescription":  {"text": "<rule.description>"},
  "defaultConfiguration": {"level": <severity-mapped>},
  "properties": {
    "category": "QUALITY",
    "tier": "TIER_2_WARN",
    "findingType": "CODE_SMELL"
  }
}
```

**Severity → SARIF level map** (SARIF only has error/warning/note):

| `Severity` | SARIF `level` | Rationale |
|---|---|---|
| BLOCKER | `error` | Scan-breakers surface as error |
| CRITICAL | `error` | Data correctness — error |
| MAJOR | `warning` | Maintainability — warning |
| MINOR | `warning` | Style — warning |
| INFO | `note` | Suggestion |

**Findings → SARIF results:**
```json
{
  "ruleId": "Q001",
  "ruleIndex": <index into tool.driver.rules>,
  "level": "warning",
  "message": {"text": "<finding.message>"},
  "locations": [{
    "physicalLocation": {
      "artifactLocation": {"uri": "<relative-posix-path>", "uriBaseId": "%SRCROOT%"},
      "region": {"startLine": 42, "startColumn": 1, "endLine": 42, "endColumn": 10}
    }
  }],
  "partialFingerprints": {"dbtcov/v1": "<finding.fingerprint>"},
  "properties": {
    "severity": "MAJOR",
    "category": "QUALITY",
    "tier": "TIER_2_WARN",
    "confidence": 0.95,
    "isNew": false,
    "nodeId": "model.analytics.stg_orders"
  }
}
```

**URI handling:**
- Paths are POSIX-form regardless of host OS (SARIF requires forward slashes).
- `uriBaseId: "%SRCROOT%"` — consumers resolve against the project root, and the invocation block declares `originalUriBaseIds: {"%SRCROOT%": {"uri": "file://<abs project root>/"}}`.
- Absolute URIs are used only in `workingDirectory` and `originalUriBaseIds`.

**`partialFingerprints` key:** `dbtcov/v1` — version-namespaced so future fingerprint changes don't mis-match baselines in code-scanning UIs. The value equals SPEC-01's `Finding.fingerprint`.

**Coverage in SARIF:** non-standard. We attach to `runs[0].properties.coverage` as an array of coverage metrics; consumers who understand SARIF ignore it, dbtcov-aware tools read it. Gate logic reads from `ScanResult` directly, not SARIF.

**Validation:** emit must validate against the local copy of SARIF 2.1.0 schema (`schemas/sarif-2.1.0.json`, vendored) in tests only — production emit does not validate (cost). A failing schema validation is a test failure, not a runtime fallback.

### 4.4 `console.py`

Uses `rich`. Output format fixed to what the plan mock-up shows:

```
dbt project: analytics (snowflake)    52 models  14 sources  3 exposures
render: MOCK  (2 uncertain, fell back to PARTIAL)
────────────────────────────────────────────────────────────────────────────
Coverage
  test   47/52  90%  ✓ (gate 80%)
  doc    38/52  73%  ✗ (gate 90%)
Tier-1 (gate-blocking)   3 findings
  CRITICAL  Q002  missing PK test       models/marts/fct_orders.sql:1   conf=1.00
  CRITICAL  P001  cross-join            models/stg/stg_users.sql:42     conf=0.95
  MAJOR     R001  91% dup of int_orders models/int/int_orders_v2.sql:1  conf=0.91
Tier-2 (warn)           11 findings   (suppressed 4 below conf 0.7)
────────────────────────────────────────────────────────────────────────────
Gate: FAIL (3 Tier-1 findings, 1 coverage regression: doc 73% < 90%)
```

```python
class ConsoleReporter:
    name = "console"
    default_filename = None

    def __init__(self, gate_config: "GateConfig | None" = None, use_color: bool = True): ...

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        """
        Writes to stdout via rich.console.Console. If `out` is a file path, renders
        to a file with color stripped (for CI logs).
        """
```

**Styling:**
- BLOCKER/CRITICAL → red bold; MAJOR → yellow; MINOR → dim yellow; INFO → cyan.
- ✓ green, ✗ red for coverage gate status.
- Coverage ratios colored: ≥gate = green, <gate = red, no-gate = default.
- Rule ids rendered as hyperlinks (`rich` OSC-8) to a docs URL template: `https://dbtcov.dev/rules/<rule_id>` — placeholder; turn off via `--no-links`.

**Grouping order:**
1. Header: project summary + render stats.
2. Coverage table (per enabled dimension).
3. Tier-1 findings (listed in order: severity DESC, file_path ASC, line ASC).
4. Tier-2 findings (same ordering).
5. Gate preview (mirrors SPEC-11 gate output).

**Suppressed findings line:** when `result` metadata includes `suppressed_below_confidence: N`, show "(suppressed N below conf 0.7)" on the tier header. If not populated, omit.

**When `ScanResult.findings == []`:**
```
No findings. Coverage ✓  Gate: PASS
```
One-line output; no tree.

### 4.5 `_shared.py`

```python
SEVERITY_RANK = {
    Severity.BLOCKER: 0, Severity.CRITICAL: 1, Severity.MAJOR: 2,
    Severity.MINOR: 3, Severity.INFO: 4,
}

def sort_findings_for_display(findings: list[Finding]) -> list[Finding]:
    """Sort by (tier order, severity rank, file_path, line)."""

def group_by_tier(findings: list[Finding]) -> dict[Tier, list[Finding]]: ...

def rule_docs_url(rule_id: str) -> str:
    return f"https://dbtcov.dev/rules/{rule_id}"
```

---

## 5. Edge cases

| Case | Expected |
|---|---|
| `result.findings == []` | Valid SARIF (empty `results`); one-line console; valid JSON |
| Finding with `end_line is None` | SARIF `region` omits `endLine` (valid); console prints only start |
| Finding with non-ASCII characters in message | Preserved in JSON/SARIF (UTF-8); console uses terminal encoding |
| `file_path` contains spaces | SARIF URI escapes them; JSON keeps literal |
| Windows host → POSIX SARIF paths | Converted via `PurePosixPath` coercion |
| Rule id appears in findings but not discovered by registry | SARIF still emits `rules[]` entry with minimal metadata (id + shortDesc=description fallback); console shows rule id unchanged |
| `INTERNAL_CRASH` rule | Treated like any other rule; `level=error`, shown in Tier-1 (since severity=BLOCKER) |
| 10k findings | All reporters stream where possible; console truncates to top 100 per tier with "(+N more, use --format=json)" footer |
| `out` is a directory not a file (file reporters) | Treat as out_dir, use `default_filename` within it |
| `out` path's parent doesn't exist | Create parent dirs (file reporters) |
| Color disabled (`NO_COLOR` env var, `--no-color`, non-tty) | rich auto-detects; explicit `--no-color` forces off |

---

## 6. Tests (`tests/unit/reporters/`)

### 6.1 `test_json_reporter.py`
- Round-trip: emit JSON → `ScanResult.model_validate_json` reproduces input exactly.
- Empty findings → valid JSON with `"findings": []`.
- File path portability: Windows-style input → POSIX-style output? **Decision:** leave as-is in JSON (it's a Pydantic-level concern, not a reporter concern); SARIF is where POSIX normalization happens.

### 6.2 `test_sarif_reporter.py`
- Output validates against vendored SARIF 2.1.0 schema.
- Severity map correct for each of 5 severities.
- `partialFingerprints["dbtcov/v1"]` equals `finding.fingerprint`.
- `rules[]` has one entry per unique rule_id.
- POSIX paths regardless of host.
- `%SRCROOT%` baseId populated; `originalUriBaseIds` set in `invocations[0]`.
- Coverage attached to `runs[0].properties.coverage`.
- SARIF with zero findings still validates.
- SARIF for a crash-only scan (only INTERNAL_CRASH findings) validates.

### 6.3 `test_console_reporter.py`
- Golden snapshot against a fixture ScanResult (use `rich.console.Console(file=StringIO(), record=True)` and compare ANSI-stripped output).
- Empty findings → one-liner snapshot.
- Non-tty: no ANSI escapes.
- Truncation at 100 per tier with "(+N more)" footer.
- Gate config None → "Gate: N/A" in footer.

### 6.4 `test_shared.py`
- Sort order deterministic and documented.
- Group by tier stable.

**Coverage target:** 92%.

---

## 7. Acceptance criteria

- [ ] JSON output round-trips through `ScanResult.model_validate_json`.
- [ ] SARIF output validates against SARIF 2.1.0 schema in a test.
- [ ] SARIF file uploads cleanly to GitHub Code Scanning (manual smoke test documented in SPEC-13 golden, not automated).
- [ ] Console output matches golden snapshot for the reference fixture.
- [ ] All three reporters implement the `Reporter` protocol and are discoverable via `reporters/__init__.py`'s `REPORTERS: dict[str, type[Reporter]]`.
- [ ] `ruff`, `mypy --strict` clean.
- [ ] ≥92% coverage on `tests/unit/reporters/`.

---

## 8. Open questions

- Should we embed rule `fullDescription` as Markdown in SARIF (SARIF allows `markdown` alongside `text`)? **Proposal:** yes in phase 2 when we have proper rule docs; phase 1 emits text only.
- Should console show `INTERNAL_CRASH` findings prominently (they indicate a dbtcov bug, not user code)? **Proposal:** yes — prefix with "⚠ dbtcov internal:" header, distinct from user findings. Tracked for refinement after first real-world usage.
- Should the JSON reporter emit newline-delimited JSON (NDJSON) for streaming? **Proposal:** no — `ScanResult` is a single document. If someone wants NDJSON per-finding, it's a trivial postprocessing step.
