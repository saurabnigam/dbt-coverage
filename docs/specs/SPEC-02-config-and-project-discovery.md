# SPEC-02 — Config & Project Discovery

**Status:** draft
**Depends on:** SPEC-01
**Blocks:** SPEC-03, SPEC-12

---

## 1. Purpose

Two responsibilities:
1. **Project discovery** — locate the dbt project root by walking up from a given path looking for `dbt_project.yml`. Extract name, profile, adapter/dialect, configured model paths.
2. **Config loading** — load, validate, and merge `dbtcov.yml` with defaults. Produce a strongly-typed `DbtcovConfig` consumed by every other module.

---

## 2. Non-goals

- No dbt-core import. Parse `dbt_project.yml` as plain YAML.
- No profile resolution (no `profiles.yml` reading, no warehouse connection). Only the adapter name is needed to resolve dialect.
- No rule-pack discovery — that lives in SPEC-07.

---

## 3. Module layout

```
src/dbt_coverage/utils/
  __init__.py
  project_discovery.py      # find_project_root, DbtProjectInfo
  config.py                 # DbtcovConfig, load_config, DEFAULTS
  dialect.py                # adapter_to_dialect mapping
```

---

## 4. API Surface

### 4.1 `project_discovery.py`

```python
from pathlib import Path
from pydantic import BaseModel

class DbtProjectInfo(BaseModel):
    root: Path                       # absolute
    name: str                        # from dbt_project.yml
    profile: str                     # profile name (for adapter lookup)
    model_paths: list[str]           # default ["models"]
    test_paths: list[str]            # default ["tests"]
    macro_paths: list[str]           # default ["macros"]
    seed_paths: list[str]            # default ["seeds"]
    target_path: str                 # default "target"
    adapter: str | None              # resolved from profile if known, else None
    dbt_version_required: str | None # from require-dbt-version, if set

def find_project_root(start: Path) -> Path:
    """Walk up from `start` until dbt_project.yml found. Raise ConfigError if none."""

def load_project_info(root: Path) -> DbtProjectInfo:
    """Parse dbt_project.yml. Adapter lookup is best-effort (see 4.3)."""
```

**Discovery rules:**
- Walk up from `start` up to `/` (or drive root on Windows).
- First directory containing `dbt_project.yml` wins.
- If `start` is a file, start search from its parent.
- Symlinks followed once; cycles detected and raise `ConfigError`.

### 4.2 `config.py`

```python
from pydantic import BaseModel, Field
from dbt_coverage.core import Severity, Tier, RenderMode

class RenderConfig(BaseModel):
    model_config = {"extra": "forbid"}
    mode: RenderMode = RenderMode.MOCK
    fallback: RenderMode | None = None        # e.g. PARTIAL; phase-2 feature

class RuleOverride(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    severity: Severity | None = None          # override default severity
    tier: Tier | None = None                  # override default tier
    confidence_min: float | None = Field(default=None, ge=0.0, le=1.0)
    params: dict = Field(default_factory=dict)  # rule-specific (e.g. duplicates.threshold)

class CoverageThresholds(BaseModel):
    model_config = {"extra": "forbid"}
    test: float | None = Field(default=None, ge=0.0, le=1.0)
    doc: float | None = Field(default=None, ge=0.0, le=1.0)
    unit: float | None = Field(default=None, ge=0.0, le=1.0)
    column: float | None = Field(default=None, ge=0.0, le=1.0)

class BaselineConfig(BaseModel):
    model_config = {"extra": "forbid"}
    path: str | None = None                   # "git:main" or file path; None = disabled
    fail_on_new_only: bool = True

class GateConfig(BaseModel):
    model_config = {"extra": "forbid"}
    fail_on_tier: Tier = Tier.TIER_1_ENFORCED
    fail_on_coverage_regression: bool = True

class DbtcovConfig(BaseModel):
    model_config = {"extra": "forbid"}
    version: int = 1
    render: RenderConfig = Field(default_factory=RenderConfig)
    dialect: str | None = None                # None → resolved from adapter
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    rules: dict[str, RuleOverride] = Field(default_factory=dict)
    coverage: CoverageThresholds = Field(default_factory=CoverageThresholds)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    gate: GateConfig = Field(default_factory=GateConfig)
    exclude: list[str] = Field(default_factory=list)   # glob patterns, relative to project_root

def load_config(
    project_root: Path,
    config_path: Path | None = None,
    cli_overrides: dict | None = None,
) -> DbtcovConfig:
    """
    Precedence (low → high):
      1. DEFAULTS (from DbtcovConfig default_factory values)
      2. dbtcov.yml in project_root (or config_path if provided)
      3. cli_overrides (e.g. {"dialect": "snowflake"} from --dialect flag)
    Raises ConfigError on schema validation failure or unknown YAML keys.
    """

def write_default_config(path: Path) -> None:
    """Write a commented dbtcov.yml scaffold. Used by `dbtcov init`."""
```

**Merging rule:** deep-merge, but `rules` dict merges per-rule (so user only needs to override specific rules). `coverage`, `baseline`, `gate`, `render` replace-merge at the field level.

### 4.3 `dialect.py`

```python
ADAPTER_TO_SQLGLOT_DIALECT: dict[str, str] = {
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "postgres": "postgres",
    "redshift": "redshift",
    "databricks": "databricks",
    "spark": "spark",
    "duckdb": "duckdb",
    "trino": "trino",
    "athena": "athena",
}

def resolve_dialect(
    config_dialect: str | None,
    adapter: str | None,
) -> str:
    """
    Precedence: config_dialect > ADAPTER_TO_SQLGLOT_DIALECT[adapter] > "postgres" (safe default).
    Returns a sqlglot-accepted dialect string.
    """
```

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| `dbt_project.yml` missing | `ConfigError("No dbt project found walking up from {start}")` |
| `dbt_project.yml` exists but invalid YAML | `ConfigError` with line number from yaml.YAMLError |
| `dbtcov.yml` missing | Use DEFAULTS; no error |
| `dbtcov.yml` has unknown top-level key | Reject (Pydantic `extra=forbid`) with the unknown key named |
| `dbtcov.yml` has unknown rule ID | Accept; SPEC-07 warns at rule-engine startup, not here |
| `version: 2` in dbtcov.yml | `ConfigError("Unsupported dbtcov.yml version 2; this binary supports 1")` |
| `confidence_threshold: 1.5` | Pydantic rejects |
| Adapter unknown in ADAPTER_TO_SQLGLOT_DIALECT | `resolve_dialect` returns "postgres" and logs a warning |
| Both `--dialect` flag and `dbtcov.yml dialect:` set | CLI flag wins (cli_overrides applied last) |
| `dbt_project.yml` has `model-paths:` (dbt ≤1.5 spelling) vs `model_paths:` | Accept both; normalize to `model_paths` |
| Project root is a git submodule | No special handling; just walk until `dbt_project.yml` found |

---

## 6. Test plan (`tests/unit/utils/`)

### 6.1 `test_project_discovery.py`
- Fixture: nested dir with `dbt_project.yml` at depth 3 → `find_project_root` returns correct path.
- No `dbt_project.yml` anywhere → raises `ConfigError`.
- Symlink cycle → raises `ConfigError`.
- `load_project_info` parses all documented fields including `model-paths` and `model_paths` synonyms.
- Missing optional field (e.g. `require-dbt-version`) → `None`.

### 6.2 `test_config.py`
- Empty project, no `dbtcov.yml` → `load_config` returns `DbtcovConfig()` (all defaults).
- Valid `dbtcov.yml` with partial rule overrides → merged correctly; non-overridden rules keep defaults.
- Unknown top-level key → raises `ConfigError` naming the key.
- `version: 2` → raises.
- CLI overrides applied last (test with `cli_overrides={"dialect": "bigquery"}` overriding yml `dialect: snowflake`).
- `write_default_config` produces a file that parses back to `DbtcovConfig()` defaults.

### 6.3 `test_dialect.py`
- Each key in `ADAPTER_TO_SQLGLOT_DIALECT` returns expected sqlglot name.
- Unknown adapter → returns `"postgres"` + warning logged.
- `config_dialect` present → always wins over adapter lookup.

**Coverage target:** 95%+.

---

## 7. Acceptance criteria

- [ ] `find_project_root(Path("examples/basic_project/models/stg"))` returns `examples/basic_project/` absolute path
- [ ] `load_config(project_root)` round-trips to JSON via Pydantic
- [ ] `ruff` + `mypy --strict` clean on `utils/`
- [ ] `pytest tests/unit/utils/` ≥95% line coverage
- [ ] Zero imports from `dbt`, `sqlglot`, `jinja2`

---

## 8. Open questions

- Should `exclude` patterns use `.gitignore` syntax or plain globs? **Proposal:** plain globs (simpler, Python `pathlib.Path.match`). Flag for discussion.
- Windows path handling in symlink detection — accept as "best effort" for v1; document the limit.
