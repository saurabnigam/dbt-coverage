"""SPEC-02 §4.2 — DbtcovConfig + load_config + write_default_config."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML

from dbt_coverage.core import AdapterMode, ConfigError, RenderMode, Severity, Tier

_LOG = logging.getLogger(__name__)
_yaml = YAML(typ="safe")


class RenderConfig(BaseModel):
    """SPEC-25 §4.6 — render mode + compiled-dir overrides.

    ``mode=AUTO`` (the new default) asks the orchestrator to switch to
    ``COMPILED`` when ``target/compiled`` exists and covers at least
    ``compiled_min_coverage`` of the discovered models; otherwise it falls
    back to ``MOCK``.
    """

    model_config = ConfigDict(extra="forbid")
    mode: RenderMode = RenderMode.AUTO
    compiled_dir: Path | None = None
    compiled_min_coverage: float = Field(default=0.5, ge=0.0, le=1.0)
    fallback: RenderMode | None = None


class RuleOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    severity: Severity | None = None
    tier: Tier | None = None
    confidence_min: float | None = Field(default=None, ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)


class CoverageThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float = Field(ge=0.0, le=1.0)


class WeightTable(BaseModel):
    """SPEC-22 §4.2 — per-class weights for test classification."""

    model_config = ConfigDict(extra="forbid")
    trivial: float = Field(default=0.0, ge=0.0, le=1.0)
    structural: float = Field(default=0.25, ge=0.0, le=1.0)
    logical: float = Field(default=1.0, ge=0.0, le=1.0)
    unknown: float = Field(default=0.0, ge=0.0, le=1.0)


class TestOverrides(BaseModel):
    """SPEC-22 §4.2 — per-test-kind reclassification (glob patterns)."""

    __test__ = False  # not a pytest test class

    model_config = ConfigDict(extra="forbid")
    logical: list[str] = Field(default_factory=list)
    structural: list[str] = Field(default_factory=list)
    trivial: list[str] = Field(default_factory=list)


_RESERVED_COVERAGE_KEYS = {
    "weights",
    "test_overrides",
    "dimensions",
    "thresholds",
    "exemptions",
}


class CoverageExemptions(BaseModel):
    """SPEC-31 §4 — per-dimension glob allow-lists (node is removed from both
    numerator and denominator)."""

    model_config = ConfigDict(extra="allow")

    test: list[str] = Field(default_factory=list)
    doc: list[str] = Field(default_factory=list)
    test_meaningful: list[str] = Field(default_factory=list)
    test_weighted_cc: list[str] = Field(default_factory=list)
    test_unit: list[str] = Field(default_factory=list)
    complexity: list[str] = Field(default_factory=list)

    def for_dimension(self, dim: str) -> list[str]:
        return list(getattr(self, dim, []) or [])


class CoverageConfig(BaseModel):
    """SPEC-22 §4.2 — composite coverage config.

    Preserves the existing ``coverage: { <dim>: {min: x}, ... }`` YAML shape
    while adding typed fields for weights, overrides, and an optional
    explicit dimension allowlist. Thresholds are surfaced via
    :pyattr:`thresholds`; they remain writable as top-level keys for back-compat.
    """

    model_config = ConfigDict(extra="allow")

    weights: WeightTable = Field(default_factory=WeightTable)
    test_overrides: TestOverrides = Field(default_factory=TestOverrides)
    dimensions: list[str] | None = None
    thresholds: dict[str, CoverageThreshold] = Field(default_factory=dict)
    exemptions: CoverageExemptions = Field(default_factory=CoverageExemptions)

    @model_validator(mode="before")
    @classmethod
    def _absorb_top_level_dims(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        thresholds = dict(out.get("thresholds") or {})
        for k in list(out.keys()):
            if k in _RESERVED_COVERAGE_KEYS:
                continue
            v = out[k]
            if isinstance(v, dict) and "min" in v and len(v) == 1:
                thresholds[k] = v
                del out[k]
        out["thresholds"] = thresholds
        return out


class ComplexityConfig(BaseModel):
    """SPEC-19 §7 / SPEC-20 — thresholds for the complexity dimension + Q003."""

    model_config = ConfigDict(extra="forbid")
    threshold_warn: int = Field(default=15, ge=1)
    threshold_block: int = Field(default=30, ge=1)
    include_jinja: bool = True
    exempt_models: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_thresholds(self) -> ComplexityConfig:
        if self.threshold_block < self.threshold_warn:
            raise ValueError(
                f"complexity.threshold_block ({self.threshold_block}) "
                f"must be >= threshold_warn ({self.threshold_warn})"
            )
        return self


class AdapterConfigYaml(BaseModel):
    """SPEC-21 §4.1 — per-adapter YAML block."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    mode: AdapterMode = AdapterMode.AUTO
    report: Path | None = None
    timeout_seconds: int = Field(default=60, ge=1)
    argv: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class BaselineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    fail_on_new_only: bool = True


class OverrideEntry(BaseModel):
    """SPEC-31 §4 — one ``overrides:`` block in dbtcov.yml."""

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    waive: list[str] = Field(default_factory=list)
    reason: str = ""
    reviewer: str | None = None
    expires: date | None = None
    # Optional stable ID (yaml anchor) so gates can cite specific entries.
    id: str | None = None

    @field_validator("expires", mode="before")
    @classmethod
    def _parse_expires(cls, v: Any) -> Any:
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            try:
                return date.fromisoformat(s)
            except ValueError:
                # accept naive ISO datetimes too
                return datetime.fromisoformat(s).date()
        return v

    @model_validator(mode="after")
    def _require_selector_and_reason(self) -> OverrideEntry:
        if not (self.paths or self.models or self.node_ids):
            raise ValueError(
                "overrides entry must set at least one of 'paths', 'models', 'node_ids'"
            )
        if not self.waive:
            raise ValueError("overrides entry must set 'waive' (rule ids or '*')")
        if not self.reason.strip():
            raise ValueError("overrides entry requires a non-empty 'reason'")
        return self


class TestingUnitTestsConfig(BaseModel):
    __test__ = False

    model_config = ConfigDict(extra="forbid")
    exempt: list[str] = Field(default_factory=list)


class TestingConfig(BaseModel):
    __test__ = False

    model_config = ConfigDict(extra="forbid")
    unit_tests: TestingUnitTestsConfig = Field(default_factory=TestingUnitTestsConfig)


class TestingThresholds(BaseModel):
    """SPEC-32 §7 — CI thresholds for test execution."""

    __test__ = False

    model_config = ConfigDict(extra="forbid")
    unexecuted_tests_max: int | None = 0


class SkipsThresholds(BaseModel):
    """SPEC-33 §5 — gate thresholds for check-skip counts. ``None`` = no enforcement."""

    model_config = ConfigDict(extra="forbid")
    parse_failed_max: int | None = None
    render_uncertain_max: int | None = None
    rule_error_max: int | None = 0
    adapter_failed_max: int | None = 0
    total_max: int | None = None


class CoverageThresholdsBlock(BaseModel):
    """SPEC-32 §7 — convenience shape for ``gate.thresholds.coverage.<dim>``."""

    model_config = ConfigDict(extra="allow")


class GateThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage: dict[str, float] = Field(default_factory=dict)
    testing: TestingThresholds = Field(default_factory=TestingThresholds)
    skips: SkipsThresholds = Field(default_factory=SkipsThresholds)


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fail_on_tier: Tier = Tier.TIER_1_ENFORCED
    fail_on_new_only: bool = False
    fail_on_coverage_regression: bool = True
    thresholds: GateThresholds = Field(default_factory=GateThresholds)


class ReporterSkipDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skip_detail: str | None = None  # summary | aggregated | per_pair


class ReportsConfig(BaseModel):
    """SPEC-33 §5 — global + per-reporter skip-detail settings."""

    model_config = ConfigDict(extra="forbid")
    skip_detail: str = "aggregated"  # summary | aggregated | per_pair
    show_suppressed: bool = False
    console: ReporterSkipDetail = Field(default_factory=ReporterSkipDetail)
    # Renamed the JSON sub-block attribute to avoid shadowing BaseModel.json().
    # The YAML key is still ``json:`` via alias.
    json_: ReporterSkipDetail = Field(default_factory=ReporterSkipDetail, alias="json")
    sarif: ReporterSkipDetail = Field(default_factory=ReporterSkipDetail)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def resolve_skip_detail(self, reporter: str) -> str:
        # Reporters name themselves "console", "json", "sarif".
        attr = "json_" if reporter == "json" else reporter
        rep = getattr(self, attr, None)
        if isinstance(rep, ReporterSkipDetail) and rep.skip_detail:
            return rep.skip_detail
        return self.skip_detail


class ArchitectureConfig(BaseModel):
    """SPEC-27 §2 — layer classification + allowed DAG edges.

    ``layers`` maps each layer name (e.g. ``staging``) to a list of glob
    patterns. The first layer whose patterns match a model's file path or
    model name wins. ``allowed_edges`` enumerates permitted layer transitions
    as two-element lists; anything else trips A001.
    """

    model_config = ConfigDict(extra="forbid")
    layers: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "source": ["sources.*"],
            "staging": ["stg_*", "staging/**"],
            "intermediate": ["int_*", "intermediate/**"],
            "mart": ["fct_*", "dim_*", "marts/**"],
        }
    )
    allowed_edges: list[list[str]] = Field(
        default_factory=lambda: [
            ["source", "staging"],
            ["staging", "intermediate"],
            ["staging", "mart"],
            ["intermediate", "mart"],
            ["intermediate", "intermediate"],
            ["mart", "mart"],
        ]
    )


class DbtcovConfig(BaseModel):
    """Top-level config. SPEC-02 §4.2 with extensions for SPEC-11 gate fields."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    render: RenderConfig = Field(default_factory=RenderConfig)
    dialect: str | None = None
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    rules: dict[str, RuleOverride] = Field(default_factory=dict)
    coverage: CoverageConfig = Field(default_factory=CoverageConfig)
    complexity: ComplexityConfig = Field(default_factory=ComplexityConfig)
    adapters: dict[str, AdapterConfigYaml] = Field(default_factory=dict)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    gate: GateConfig = Field(default_factory=GateConfig)
    exclude: list[str] = Field(default_factory=list)
    # SPEC-31 §4 — reviewer-attested suppressions.
    overrides: list[OverrideEntry] = Field(default_factory=list)
    # SPEC-32 §7 — test execution config (exempt models, etc.).
    testing: TestingConfig = Field(default_factory=TestingConfig)
    # SPEC-33 §5 — report rendering (skip detail) preferences.
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    # SPEC-27 §2 — architecture layer classification + allowed edges.
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)


# --------------------------------------------------------------------------- helpers


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: override wins. Rule dicts merge per-rule."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce_rules_block(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize the 'rules:' yaml block to {rule_id: override_dict}."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("'rules' must be a mapping of rule-id -> override block")
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if v is None:
            v = {}
        if not isinstance(v, dict):
            raise ConfigError(f"rules.{k}: override must be a mapping, got {type(v).__name__}")
        out[str(k)] = v
    return out


# --------------------------------------------------------------------------- API


def load_config(
    project_root: Path,
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> DbtcovConfig:
    """Load `dbtcov.yml`, merge with CLI overrides, validate.

    Precedence (low → high): defaults → dbtcov.yml → cli_overrides.
    """
    file_data: dict[str, Any] = {}
    resolved_path: Path | None = None
    if config_path is not None:
        resolved_path = Path(config_path)
    else:
        default_path = Path(project_root) / "dbtcov.yml"
        if default_path.exists():
            resolved_path = default_path

    if resolved_path is not None:
        try:
            file_data = _yaml.load(resolved_path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError as e:
            raise ConfigError(f"Config file not found: {resolved_path}") from e
        except Exception as e:
            raise ConfigError(f"Invalid YAML in {resolved_path}: {e}") from e

    if not isinstance(file_data, dict):
        raise ConfigError(f"{resolved_path}: top-level must be a mapping")

    if "version" in file_data and file_data["version"] not in (1, "1"):
        raise ConfigError(
            f"Unsupported dbtcov.yml version {file_data['version']!r}; this binary supports 1"
        )

    if "rules" in file_data:
        file_data["rules"] = _coerce_rules_block(file_data["rules"])

    merged: dict[str, Any] = {}
    if file_data:
        merged = _deep_merge(merged, file_data)
    if cli_overrides:
        cleaned = {k: v for k, v in cli_overrides.items() if v is not None}
        merged = _deep_merge(merged, cleaned)

    try:
        return DbtcovConfig(**merged)
    except ValidationError as e:
        raise ConfigError(f"dbtcov config validation error: {e}") from e


def write_default_config(path: Path) -> None:
    """Write the shipped `dbtcov.yml.template` contents to ``path``."""
    from importlib import resources

    template = resources.files("dbt_coverage.templates").joinpath("dbtcov.yml.template")
    Path(path).write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
