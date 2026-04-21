# SPEC-31 — Waivers + baseline

## 1. Problem

Reviewers need a way to say *"I've reviewed this finding, it's intentional, don't fail CI"* without deleting the finding from the report (audit trail) and without touching the offending SQL. Equivalent to SonarQube's *"Won't Fix"* / JaCoCo's baseline.

## 2. Principles

- **Everything driven from `dbtcov.yml`** — no inline SQL pragmas, no schema.yml annotations.
- **SonarQube semantics** — suppressed findings stay in JSON/SARIF with a `Suppression{reason, reviewer, expires}` block; console hides by default (`--show-suppressed` reveals).
- **Waivers expire** — `expires` is optional but when set and past → re-activate the finding *and* emit `G003 waiver expired`.
- **Baseline = bulk waiver** — `.dbtcov/baseline.json` snapshots findings so legacy projects can adopt without refactoring first.

## 3. Core types

Added to [src/dbt_coverage/core/models.py](../../src/dbt_coverage/core/models.py):

```python
class SuppressionSource(StrEnum):
    OVERRIDE = "override"      # dbtcov.yml overrides block
    BASELINE = "baseline"      # .dbtcov/baseline.json
    EXEMPTION = "exemption"    # coverage.exemptions — applies to coverage dims, not findings

class Suppression(BaseModel):
    source: SuppressionSource
    reason: str
    reviewer: str | None = None
    expires: date | None = None
    entry_id: str | None = None

class Finding(BaseModel):
    # existing fields...
    suppressed: bool = False
    suppression: Suppression | None = None
```

## 4. `dbtcov.yml` schema

```yaml
overrides:
  - paths: ["models/legacy/**/*.sql"]
    models: ["stg_deprecated_*"]
    node_ids: ["model.proj.raw_account"]
    waive: [Q001, P002]          # or "*" for all rules
    reason: "legacy audit tables under deprecation"    # REQUIRED
    reviewer: "saurabh"
    expires: "2026-09-30"

coverage:
  exemptions:
    test_meaningful: ["stg_raw_copy_*"]
    test_weighted_cc: []
    doc: []
```

**Matching**: an override matches a finding when
`(rule_id in waive OR "*" in waive) AND (path glob match OR model glob match OR node_id exact match)`.
At least one selector is required; `reason` is required.

## 5. Baseline file

`.dbtcov/baseline.json`:

```json
{
  "schema_version": 1,
  "captured_at": "2026-04-18T10:00:00Z",
  "dbtcov_version": "0.5.0",
  "entries": [
    {"fingerprint": "b3c9…", "rule_id": "Q003", "node_id": "model.proj.fct_x", "path": "models/marts/fct_x.sql", "reason": "baselined"}
  ]
}
```

CLI:
- `dbtcov baseline capture [--out .dbtcov/baseline.json]`
- `dbtcov baseline diff [--baseline .dbtcov/baseline.json]`

`scan` and `gate` accept `--baseline PATH` and treat matching fingerprints as suppressed with `source=BASELINE`.

## 6. WaiverResolver

New module `src/dbt_coverage/analyzers/waivers.py`:

```python
class WaiverResolver:
    def __init__(self, config: DbtcovConfig, baseline: BaselineFile | None, today: date): ...

    def apply(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """Returns (stamped_findings, extra_findings).
        extra_findings contains G003 waiver-expired findings.
        """
```

Precedence: `overrides` first, then `baseline`. Expired overrides produce G003 **and** un-suppress the underlying finding.

## 7. Orchestrator hook

In [src/dbt_coverage/cli/orchestrator.py](../../src/dbt_coverage/cli/orchestrator.py), after `engine.run(...)` and before `compute_all(...)` / `_build_model_summaries(...)`:

```python
resolver = WaiverResolver(config, baseline, today=date.today())
findings, governance_extra = resolver.apply(findings)
findings.extend(governance_extra)
```

## 8. Report behavior

- **Gate**: skips `f for f in findings if not f.suppressed` — suppressed findings never fail the gate.
- **Score** (`_build_model_summaries`): same filter — suppressed findings do not penalise score.
- **Coverage**: `coverage.exemptions` globs are honored by [src/dbt_coverage/coverage/aggregator.py](../../src/dbt_coverage/coverage/aggregator.py); exempted nodes are removed from both numerator and denominator of that dimension.
- **SARIF**: `results[].suppressions[]` emitted per SARIF 2.1.0 §3.27.23 (`kind="external", status="accepted", justification=<reason>`).
- **JSON**: `Finding.suppression` serialises inline.
- **Console**: suppressed findings hidden by default; `--show-suppressed` reveals a dimmed "Accepted" section.
- **`dbtcov models`**: new `Waived` column between `Findings` and `File`.

## 9. Tests

- `tests/unit/analyzers/test_waivers.py` — glob matching, expiry → G003, precedence.
- `tests/integration/test_waiver_sarif.py` — golden SARIF with `suppressions[]`.
- `tests/integration/test_waiver_gate.py` — scan fails; add override → passes; fast-forward expires → fails + G003.
