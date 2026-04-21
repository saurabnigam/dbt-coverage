# SPEC-11 — Quality Gate

**Status:** draft
**Depends on:** SPEC-01, SPEC-02, SPEC-07, SPEC-09a, SPEC-10a
**Blocks:** SPEC-12

---

## 1. Purpose

Evaluate a `ScanResult` against the configured thresholds and return a pass/fail decision with human-readable reasons. The gate is the CI-enforceable contract: Tier-1 findings + coverage minimums determine exit code.

Baseline-aware gating (fail only on **new** findings) is designed here but fully wired in SPEC-19 (baseline diff).

---

## 2. Non-goals

- No baseline diff implementation — gate consumes `finding.is_new` as already computed. SPEC-19 populates it.
- No severity promotion/demotion — config overrides are applied earlier (SPEC-07 registry).
- No exit-code decision — the gate returns a `GateResult`; the CLI (SPEC-12) translates to exit code.
- No output formatting — reporters handle UI. Gate returns structured reasons.

---

## 3. Module layout

```
src/dbt_coverage/quality_gates/
  __init__.py
  gate.py                   # evaluate() + GateResult
  gate_config.py            # GateConfig — pulled from DbtcovConfig; defaults defined here
```

---

## 4. API Surface

### 4.1 `gate_config.py`

```python
from pydantic import BaseModel, Field
from dbt_coverage.core import Tier

class CoverageThreshold(BaseModel):
    model_config = {"extra": "forbid"}
    min: float = Field(ge=0.0, le=1.0)         # e.g. 0.80 for 80%

class GateConfig(BaseModel):
    model_config = {"extra": "forbid"}

    fail_on_tier: Tier = Tier.TIER_1_ENFORCED
    fail_on_new_only: bool = False               # requires baseline; ignored if no baseline used
    fail_on_coverage_regression: bool = True

    coverage: dict[str, CoverageThreshold] = Field(default_factory=dict)
    # keys: "test", "doc", "unit", "column", "pii"

    @classmethod
    def from_dbtcov(cls, cfg: "DbtcovConfig") -> "GateConfig":
        """Extract gate-relevant slice from the full config."""
```

**Defaults applied when the user omits a section:**
- `fail_on_tier = TIER_1_ENFORCED`
- `fail_on_new_only = False`
- `fail_on_coverage_regression = True`
- `coverage = {}` — no thresholds means coverage doesn't gate; findings-only gating.

### 4.2 `gate.py`

```python
from dataclasses import dataclass
from dbt_coverage.core import ScanResult, Finding, Severity, Tier
from .gate_config import GateConfig

@dataclass(frozen=True)
class GateReason:
    code: str                          # machine-readable: "TIER_1_FINDING", "COVERAGE_BELOW_MIN", ...
    message: str                       # human-readable
    offending: list[str] | None = None # rule ids, dimension names, etc.

@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[GateReason]          # non-empty iff passed==False
    counted_findings: int              # findings that counted toward the gate
    suppressed_findings: int           # findings dropped (e.g. not-new with fail_on_new_only)

def evaluate(result: ScanResult, cfg: GateConfig) -> GateResult:
    """
    Deterministic. No I/O. Pure function of (ScanResult, GateConfig).
    Multiple reasons may accumulate — we don't stop at first.
    """
```

### 4.3 Gate logic (pseudocode)

```python
def evaluate(result, cfg):
    reasons: list[GateReason] = []
    counted = 0
    suppressed = 0

    # 1. Finding-based gate
    gate_tier_rank = TIER_RANK[cfg.fail_on_tier]   # T1=0, T2=1
    offending_ids: set[str] = set()
    for f in result.findings:
        if cfg.fail_on_new_only and not f.is_new:
            suppressed += 1
            continue
        if TIER_RANK[f.tier] <= gate_tier_rank:
            counted += 1
            offending_ids.add(f.rule_id)
    if offending_ids:
        reasons.append(GateReason(
            code="FINDINGS_AT_OR_ABOVE_TIER",
            message=f"{counted} finding(s) at tier {cfg.fail_on_tier} or higher",
            offending=sorted(offending_ids),
        ))

    # 2. Coverage gate
    if cfg.fail_on_coverage_regression:
        by_dim = {m.dimension: m for m in result.coverage}
        for dim, thresh in cfg.coverage.items():
            metric = by_dim.get(dim)
            if metric is None:
                # Dimension configured but not computed — warn, don't fail.
                continue
            if metric.ratio < thresh.min:
                reasons.append(GateReason(
                    code="COVERAGE_BELOW_MIN",
                    message=f"{dim} coverage {metric.ratio:.0%} < min {thresh.min:.0%}",
                    offending=[dim],
                ))

    return GateResult(
        passed=len(reasons) == 0,
        reasons=reasons,
        counted_findings=counted,
        suppressed_findings=suppressed,
    )
```

**TIER_RANK table (constants module):**
```python
TIER_RANK = {Tier.TIER_1_ENFORCED: 0, Tier.TIER_2_WARN: 1}
```
Lower rank = more strict. `fail_on_tier` threshold means "fail on findings at this tier rank OR lower (stricter)". With default `TIER_1_ENFORCED` (rank 0), only T1 findings count. If user escalates to `TIER_2_WARN` (rank 1), both T1 and T2 count.

---

## 5. Config file integration

Extends `dbtcov.yml` (SPEC-02) with:

```yaml
coverage:
  test: { min: 0.80 }
  doc:  { min: 0.90 }
gate:
  fail_on_tier: TIER_1_ENFORCED
  fail_on_new_only: false
  fail_on_coverage_regression: true
```

**Parsing:** `DbtcovConfig` (SPEC-02) owns the top-level keys; `GateConfig.from_dbtcov` extracts `gate.*` and `coverage.*`. If both sections are omitted, defaults apply and gate behavior is: fail on any T1 finding, ignore coverage (no thresholds defined).

**Unknown keys under `gate:` or `coverage:`** → `extra="forbid"` on the Pydantic model raises a validation error at config load time (SPEC-02), preventing silent typos.

---

## 6. Edge cases

| Case | Expected |
|---|---|
| No findings, no coverage thresholds | PASS, `reasons=[]` |
| No findings, coverage 100%, thresholds set | PASS |
| 1 T1 finding, no coverage thresholds | FAIL with `FINDINGS_AT_OR_ABOVE_TIER` |
| 1 T2 finding, `fail_on_tier=TIER_1_ENFORCED` | PASS (T2 doesn't count) |
| 1 T2 finding, `fail_on_tier=TIER_2_WARN` | FAIL (user escalated) |
| 1 T1 finding marked `is_new=False`, `fail_on_new_only=True` | PASS (pre-existing finding suppressed) |
| `fail_on_new_only=True`, no baseline was applied → all findings have default `is_new=False` | No findings count → PASS. **This is a footgun.** Document: `fail_on_new_only` without `--baseline` is a misconfig. The CLI warns at invocation time (SPEC-12). Gate itself doesn't know whether a baseline was applied — that's a layering boundary. |
| Coverage dimension present in config but not computed | Logged warning, not gate failure |
| Coverage dimension computed but not in config | No gate, just informational in reporters |
| `coverage.test.min = 0` | Always pass (vacuous) |
| `coverage.test.min = 1.0`, actual 99.9% | FAIL |
| Multiple reasons | All reported, `passed=False` |
| `ScanResult.findings` contains an `INTERNAL_CRASH` finding (BLOCKER severity, T1) | Counts toward gate — dbtcov bugs fail the gate loudly. This is intentional. |
| Tier enum extended (e.g. future TIER_3_INFO) | TIER_RANK map must be updated; missing key → `KeyError` at evaluate. Acceptable — fail fast on config drift. |

---

## 7. Tests (`tests/unit/quality_gates/`)

### 7.1 `test_gate_config.py`
- Defaults populate when YAML omits gate/coverage sections.
- `from_dbtcov` extracts correctly.
- Unknown `gate:` key → validation error via Pydantic.
- `CoverageThreshold.min` outside [0,1] → validation error.

### 7.2 `test_gate.py`
- Empty result + empty config → PASS.
- One T1 finding → FAIL, one reason, offending includes rule_id.
- Two findings with same rule_id → offending list deduped.
- `fail_on_new_only=True` + all is_new=False → PASS, suppressed > 0.
- `fail_on_new_only=True` + 1 new T1 finding + 3 pre-existing → FAIL, counted=1, suppressed=3.
- Coverage below min → FAIL, offending includes dimension.
- Coverage above min → PASS.
- Coverage dimension in config but absent from result → PASS, warning logged.
- Multiple failures (T1 + coverage) → both reasons present.
- INTERNAL_CRASH finding counts toward gate.
- Tier escalation: T2 finding, `fail_on_tier=TIER_2_WARN` → FAIL.

**Coverage target:** 95%.

---

## 8. Acceptance criteria

- [ ] `evaluate()` is a pure function (no I/O, no side effects).
- [ ] `GateResult.reasons` is ordered deterministically: FINDINGS reason first, then COVERAGE reasons alphabetically by dimension.
- [ ] A T1 finding on a config with default gate settings produces `passed=False`.
- [ ] Coverage regression and finding failure can both surface in one call.
- [ ] `fail_on_new_only=True` with no `is_new=True` findings produces `passed=True` but `suppressed > 0` (visible to operator).
- [ ] `ruff`, `mypy --strict` clean.
- [ ] ≥95% coverage on `tests/unit/quality_gates/`.

---

## 9. Open questions

- Should the gate emit its own `Finding` for coverage failures (so they appear in SARIF)? **Proposal:** yes but in a later spec — phase-1 keeps gate reasons separate from findings, surfacing only via console and CLI exit. SARIF consumers get coverage via `runs[0].properties.coverage`. Revisit if a user asks for SARIF-native coverage alerts.
- Should `fail_on_new_only` detect the footgun (config true but no baseline applied) and warn via `GateResult`? **Proposal:** yes — add `warnings: list[str]` to `GateResult` in a follow-up, populated by the CLI layer. Phase-1 keeps it out of the gate and puts the warning in the CLI command.
- Escalation rules (e.g. "T2 finding gets promoted to T1 after 30 days")? **Proposal:** defer indefinitely. Too much policy surface; easier to achieve via baseline diff and explicit rule tier overrides.
