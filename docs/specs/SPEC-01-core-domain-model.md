# SPEC-01 — Core Domain Model

**Status:** draft (awaiting sign-off)
**Depends on:** —
**Blocks:** SPEC-02, SPEC-03, SPEC-05, SPEC-06, SPEC-07, SPEC-09, SPEC-10, SPEC-11, SPEC-12, SPEC-18

---

## 1. Purpose

Define the stable, serializable, typed data structures every other module depends on:
- `Finding` — a single rule violation
- `CoverageMetric` — one dimension of coverage (test/doc/unit/column)
- `ScanResult` — aggregate output of one scan
- `ParsedNode` — rendered + parsed dbt model (produced by SPEC-05/06, consumed by SPEC-07/18)
- Enum types: `Severity`, `Category`, `FindingType`, `Tier`, `RenderMode`
- Supporting types: `RenderStats`, `ColumnDiff`, `ProjectIndex` (forward-declared; fleshed out by SPEC-02/03)

These types form the **public API** between modules and the **on-disk format** for JSON/SARIF/baseline. Breaking them breaks everything downstream — so this spec is strict.

---

## 2. Non-goals

- **No I/O logic** — models are plain Pydantic BaseModels; no file reads, no network calls.
- **No business logic** — no coverage math, no rule execution, no rendering. Pure data.
- **No dbt-core types** — zero imports from `dbt`. We model what we need independently.

---

## 3. Module layout

```
src/dbt_coverage/core/
  __init__.py          # re-exports public types
  enums.py             # Severity, Category, FindingType, Tier, RenderMode
  models.py            # Finding, CoverageMetric, ScanResult, RenderStats
  parsed_node.py       # ParsedNode, ColumnDiff (separate to avoid sqlglot import cost if unused)
  exceptions.py        # DbtCovError hierarchy
  fingerprint.py       # stable hash for Finding.fingerprint
```

### 3.1 `core/__init__.py` — public re-exports

```python
from .enums import Severity, Category, FindingType, Tier, RenderMode
from .models import Finding, CoverageMetric, ScanResult, RenderStats
from .parsed_node import ParsedNode, ColumnDiff
from .exceptions import DbtCovError, ConfigError, RenderError, ParseError
from .fingerprint import compute_fingerprint

__all__ = [
    "Severity", "Category", "FindingType", "Tier", "RenderMode",
    "Finding", "CoverageMetric", "ScanResult", "RenderStats",
    "ParsedNode", "ColumnDiff",
    "DbtCovError", "ConfigError", "RenderError", "ParseError",
    "compute_fingerprint",
]
```

---

## 4. API Surface

### 4.1 Enums (`core/enums.py`)

```python
from enum import StrEnum

class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"

class Category(StrEnum):
    QUALITY = "QUALITY"
    PERFORMANCE = "PERFORMANCE"
    REFACTOR = "REFACTOR"
    SECURITY = "SECURITY"
    COVERAGE = "COVERAGE"
    GOVERNANCE = "GOVERNANCE"

class FindingType(StrEnum):
    BUG = "BUG"
    VULNERABILITY = "VULNERABILITY"
    CODE_SMELL = "CODE_SMELL"
    COVERAGE = "COVERAGE"
    GOVERNANCE = "GOVERNANCE"

class Tier(StrEnum):
    TIER_1_ENFORCED = "TIER_1_ENFORCED"
    TIER_2_WARN = "TIER_2_WARN"

class RenderMode(StrEnum):
    MOCK = "MOCK"
    PARTIAL = "PARTIAL"
    DBT = "DBT"
```

**Why `StrEnum`:** JSON-serializes to strings natively (`"CRITICAL"`, not `2`), stable across Python versions, works with Pydantic v2 round-trips without custom validators.

### 4.2 `Finding` (`core/models.py`)

```python
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

class Finding(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    rule_id: str                             # e.g. "Q001"
    severity: Severity
    category: Category
    type: FindingType
    tier: Tier
    confidence: float = Field(ge=0.0, le=1.0)
    message: str
    file_path: Path                          # relative to project_root
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    end_line: int | None = None
    end_column: int | None = None
    node_id: str | None = None               # dbt unique_id if known
    fingerprint: str                         # stable hash, see 4.7
    is_new: bool = False                     # set by baseline diff (SPEC-19)
    fix_hint: str | None = None              # populated by AI (phase 3)

    @field_validator("file_path")
    @classmethod
    def _relative_path(cls, v: Path) -> Path:
        if v.is_absolute():
            raise ValueError(f"file_path must be relative to project_root, got {v}")
        return v
```

**Invariants enforced:**
- `0.0 ≤ confidence ≤ 1.0`
- `line ≥ 1`, `column ≥ 1` (1-indexed, SARIF convention)
- `file_path` is relative (absolute paths would leak CI hostnames into SARIF)
- Immutable (`frozen=True`) — safe to use as dict keys, deduplicate via set
- `extra="forbid"` — typos in field names fail loudly

### 4.3 `CoverageMetric`

```python
from typing import Literal

class CoverageMetric(BaseModel):
    model_config = {"extra": "forbid"}

    dimension: Literal["test", "doc", "unit", "column", "pii"]
    covered: int = Field(ge=0)
    total: int = Field(ge=0)
    ratio: float = Field(ge=0.0, le=1.0)
    per_node: dict[str, tuple[int, int]] = Field(default_factory=dict)

    @field_validator("ratio")
    @classmethod
    def _ratio_matches(cls, v: float, info) -> float:
        covered, total = info.data.get("covered"), info.data.get("total")
        if covered is not None and total is not None and total > 0:
            expected = covered / total
            if abs(v - expected) > 1e-9:
                raise ValueError(f"ratio {v} != covered/total {expected}")
        return v
```

**`per_node` shape:** `{node_id: (covered_in_node, total_in_node)}`. Used by reporters to show per-model breakdowns.

**Edge case:** `total == 0` → `ratio == 0.0` by convention (not NaN, not 1.0). Reporters render as `"N/A"`.

### 4.4 `ScanResult`

```python
class ScanResult(BaseModel):
    model_config = {"extra": "forbid"}

    findings: list[Finding] = Field(default_factory=list)
    coverage: list[CoverageMetric] = Field(default_factory=list)
    project_root: Path                       # absolute; stripped in file_path
    dbt_version_detected: str | None = None
    dialect: str                             # e.g. "snowflake"
    render_stats: RenderStats
    scan_duration_ms: int = Field(ge=0)
    schema_version: int = 1                  # bump on breaking change
```

### 4.5 `RenderStats`

```python
class RenderStats(BaseModel):
    model_config = {"extra": "forbid"}

    total_files: int = Field(ge=0)
    rendered_mock: int = Field(ge=0)
    rendered_partial: int = Field(ge=0)
    rendered_dbt: int = Field(ge=0)
    render_uncertain: int = Field(ge=0)      # files flagged uncertain
    parse_success: int = Field(ge=0)
    parse_failed: int = Field(ge=0)
```

### 4.6 `ParsedNode` (`core/parsed_node.py`)

```python
from typing import Any

class ParsedNode(BaseModel):
    model_config = {"extra": "forbid", "arbitrary_types_allowed": True}

    file_path: Path                          # relative to project_root
    node_id: str | None = None               # dbt unique_id if known
    source_sql: str
    rendered_sql: str
    ast: Any | None = None                   # sqlglot.exp.Expression; Any to avoid import
    line_map: dict[int, int] = Field(default_factory=dict)  # rendered_line -> source_line
    config: dict[str, Any] = Field(default_factory=dict)    # captured from {{ config(...) }}
    refs: list[str] = Field(default_factory=list)           # __REF_*__ identifiers
    sources: list[tuple[str, str]] = Field(default_factory=list)
    macros_used: list[str] = Field(default_factory=list)
    render_mode: RenderMode
    render_uncertain: bool = False
    parse_success: bool = True
    parse_error: str | None = None

class ColumnDiff(BaseModel):
    model_config = {"extra": "forbid"}

    declared_only: list[str] = Field(default_factory=list)  # in YAML, not in SQL
    actual_only: list[str] = Field(default_factory=list)    # in SQL, not in YAML
    matching: list[str] = Field(default_factory=list)
```

**Why `Any` for `ast`:** sqlglot's `Expression` is not a Pydantic-friendly type; we don't want `core` to import sqlglot. Downstream modules cast appropriately.

**Why split `parsed_node.py`:** lets `Finding`/`ScanResult` users skip loading `ParsedNode` (which indirectly may drag in sqlglot type hints via rules). Small win, keeps import graph clean.

### 4.7 Fingerprint (`core/fingerprint.py`)

```python
import hashlib

def compute_fingerprint(
    rule_id: str,
    file_path: Path,
    code_context: str,
) -> str:
    """
    Stable hash for baseline-diffing Findings across scans.

    Intentionally excludes line/column numbers so cosmetic reformatting
    (added import, newline above) doesn't churn fingerprints.

    code_context: the normalized SQL snippet triggering the finding
                  (rule implementations are responsible for passing a stable string;
                   SPEC-07 will define the normalization helper).
    """
    h = hashlib.sha256()
    h.update(rule_id.encode())
    h.update(b"\0")
    h.update(str(file_path).encode())
    h.update(b"\0")
    h.update(code_context.encode())
    return h.hexdigest()[:16]
```

**Why sha256 truncated to 16 chars:** collision-safe for realistic project sizes (~10⁴ findings), short enough to show in console.

### 4.8 Exceptions (`core/exceptions.py`)

```python
class DbtCovError(Exception):
    """Base for all dbt-coverage-lib errors."""

class ConfigError(DbtCovError): ...
class RenderError(DbtCovError): ...
class ParseError(DbtCovError): ...
```

Kept minimal — specific modules extend this hierarchy as they need.

---

## 5. Serialization contract

All top-level types (`Finding`, `CoverageMetric`, `ScanResult`, `ParsedNode`) **must** round-trip through JSON:

```python
payload = result.model_dump_json()
restored = ScanResult.model_validate_json(payload)
assert restored == result
```

This is tested in §7.2. Why it matters: baseline files, SARIF output, and the `dbtcov report` re-emit command all depend on this.

**`Path` serialization:** Pydantic v2 serializes `Path` as a string by default — acceptable. SARIF reporter converts to `uri` strings separately.

**`ast` field:** excluded from JSON via `model_dump(exclude={"ast"})` in the ScanResult serializer. `ParsedNode` is an in-memory type; not part of baseline format.

---

## 6. Edge cases & failure modes

| Case | Expected behavior |
|---|---|
| Empty scan (no models found) | `ScanResult(findings=[], coverage=[], render_stats=RenderStats(total_files=0, ...))` — valid, gate passes |
| `Finding` with `end_line < line` | Pydantic validator rejects |
| `CoverageMetric` with `total=0` | Valid; `ratio=0.0`; reporters render "N/A" |
| `CoverageMetric` with `covered > total` | Rejected by validator (field_validator added below) |
| Duplicate findings (same fingerprint) | SPEC-07 dedups; domain model doesn't prevent — frozen=True allows set-based dedup |
| Absolute `file_path` | Rejected |
| `confidence = 0.0` | Valid but SPEC-11 suppresses below `confidence_threshold` |
| Pydantic v1 JSON format | Not supported — v2 only |
| `dialect` unknown | Domain model accepts any string; SPEC-06 validates |

**Additional validator on `CoverageMetric`:**

```python
@field_validator("total")
@classmethod
def _total_ge_covered(cls, v: int, info) -> int:
    covered = info.data.get("covered", 0)
    if v < covered:
        raise ValueError(f"total {v} < covered {covered}")
    return v
```

---

## 7. Test plan (`tests/unit/core/`)

### 7.1 Enum tests (`test_enums.py`)
- Each enum member serializes to expected string.
- Enum round-trips through `StrEnum(value)`.
- Attempting to create an enum from an invalid string raises `ValueError`.

### 7.2 Round-trip tests (`test_roundtrip.py`)
- `Finding` → JSON → `Finding` equality
- `CoverageMetric` → JSON → equality
- `ScanResult` with 3 findings + 2 coverage metrics → JSON → equality
- `ParsedNode` excluded from round-trip (in-memory only — covered by a unit test asserting `ast` excluded)

### 7.3 Validator tests (`test_validators.py`)
- `Finding` with `confidence=1.5` → raises
- `Finding` with `confidence=-0.1` → raises
- `Finding` with `line=0` → raises
- `Finding` with absolute path → raises
- `Finding` frozen — mutation raises
- `CoverageMetric` with `covered=5, total=3` → raises
- `CoverageMetric` with `covered=5, total=10, ratio=0.7` → raises (ratio mismatch)
- `CoverageMetric` with `covered=0, total=0, ratio=0.0` → accepted

### 7.4 Fingerprint tests (`test_fingerprint.py`)
- Same `(rule_id, file_path, code_context)` → same fingerprint
- Different `rule_id` → different fingerprint
- Different `code_context` → different fingerprint
- Fingerprint is 16 chars, hex

### 7.5 Exception tests (`test_exceptions.py`)
- `ConfigError` is `DbtCovError`
- All custom exceptions are catchable via `DbtCovError`

**Coverage target:** 100% (domain model is small; no excuse for gaps).

---

## 8. Acceptance criteria

- [ ] All files in §3 exist and match §4 signatures
- [ ] `from dbt_coverage.core import Finding, ScanResult, Severity, ...` works (all names in `__all__`)
- [ ] `pytest tests/unit/core/` passes with 100% line + branch coverage
- [ ] `mypy --strict src/dbt_coverage/core/` passes (no `Any` leaks except `ParsedNode.ast`)
- [ ] `ruff check src/dbt_coverage/core/` passes
- [ ] JSON round-trip test passes for `ScanResult` containing all enum variants
- [ ] Zero imports from `dbt`, `sqlglot`, `jinja2`, `sqlfluff` in this module
- [ ] `python -c "from dbt_coverage.core import *; import json; print(json.dumps({}))"` runs without side effects (no warehouse connections, no file reads)

---

## 9. Open questions

None — this spec is self-contained. Awaiting user sign-off to implement.

---

## 10. After sign-off

Tasks for the implementation pass:
1. Create `src/dbt_coverage/core/*.py` per §3 and §4.
2. Create `tests/unit/core/*.py` per §7.
3. Add `core` as `[tool.coverage.report].include` target in `pyproject.toml` (pyproject itself created alongside this spec's implementation — minimal deps: `pydantic>=2.0`).
4. Run acceptance checklist; check each box in this file.
