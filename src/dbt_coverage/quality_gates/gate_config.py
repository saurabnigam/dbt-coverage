"""SPEC-11 §4.1 — GateConfig (the gate-relevant slice of DbtcovConfig)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dbt_coverage.core import Tier

if TYPE_CHECKING:
    from dbt_coverage.utils import DbtcovConfig


class CoverageThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float = Field(ge=0.0, le=1.0)


class TestingThresholds(BaseModel):
    """SPEC-32 §7 — gate enforcement for test-execution gaps."""

    __test__ = False

    model_config = ConfigDict(extra="forbid")
    unexecuted_tests_max: int | None = 0


class SkipThresholds(BaseModel):
    """SPEC-33 §7 — gate enforcement for check-skip counts."""

    model_config = ConfigDict(extra="forbid")
    parse_failed_max: int | None = None
    render_uncertain_max: int | None = None
    rule_error_max: int | None = 0
    adapter_failed_max: int | None = 0
    total_max: int | None = None


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fail_on_tier: Tier = Tier.TIER_1_ENFORCED
    fail_on_new_only: bool = False
    fail_on_coverage_regression: bool = True

    coverage: dict[str, CoverageThreshold] = Field(default_factory=dict)
    testing: TestingThresholds = Field(default_factory=TestingThresholds)
    skips: SkipThresholds = Field(default_factory=SkipThresholds)

    @classmethod
    def from_dbtcov(cls, cfg: DbtcovConfig) -> GateConfig:
        return cls(
            fail_on_tier=cfg.gate.fail_on_tier,
            fail_on_new_only=cfg.gate.fail_on_new_only,
            fail_on_coverage_regression=cfg.gate.fail_on_coverage_regression,
            coverage={
                dim: CoverageThreshold(min=t.min)
                for dim, t in cfg.coverage.thresholds.items()
            },
            testing=TestingThresholds(
                unexecuted_tests_max=cfg.gate.thresholds.testing.unexecuted_tests_max,
            ),
            skips=SkipThresholds(
                parse_failed_max=cfg.gate.thresholds.skips.parse_failed_max,
                render_uncertain_max=cfg.gate.thresholds.skips.render_uncertain_max,
                rule_error_max=cfg.gate.thresholds.skips.rule_error_max,
                adapter_failed_max=cfg.gate.thresholds.skips.adapter_failed_max,
                total_max=cfg.gate.thresholds.skips.total_max,
            ),
        )
