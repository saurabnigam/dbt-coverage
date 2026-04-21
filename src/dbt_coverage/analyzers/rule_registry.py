"""SPEC-07 §4.2 — rule discovery + override resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from dbt_coverage.core import ConfigError, Severity, Tier
from dbt_coverage.utils import DbtcovConfig

_LOG = logging.getLogger(__name__)


@dataclass
class RegisteredRule:
    rule_cls: type
    enabled: bool
    effective_severity: Severity
    effective_tier: Tier
    effective_confidence_min: float
    params: dict[str, Any]


def _builtin_rule_classes() -> list[type]:
    """Hard-coded list of MVP built-in rules (avoids entry-point overhead)."""
    from .duplicates import R001DuplicateModelsRule
    from .packs.architecture.a001_layer_violation import A001LayerViolationRule
    from .packs.architecture.a002_fan_in import A002FanInRule
    from .packs.architecture.a003_direct_source import A003DirectSourceBypassRule
    from .packs.architecture.a004_cycle import A004CircularDepRule
    from .packs.architecture.a005_leaky_abstraction import A005LeakyAbstractionRule
    from .packs.performance.p001_cross_join import P001CrossJoinRule
    from .packs.performance.p002_non_sargable import P002NonSargableRule
    from .packs.performance.p003_self_join_inequality import P003SelfJoinInequalityRule
    from .packs.performance.p004_unbounded_window import P004UnboundedWindowRule
    from .packs.performance.p005_count_distinct_over import P005CountDistinctOverRule
    from .packs.performance.p006_fan_out_join import P006FanOutJoinRule
    from .packs.performance.p007_order_by_without_limit import P007OrderByNoLimitRule
    from .packs.performance.p008_deep_cte_chain import P008DeepCteChainRule
    from .packs.performance.p009_over_referenced_view import P009OverReferencedViewRule
    from .packs.performance.p010_incremental_missing_key import (
        P010IncrementalMissingKeyRule,
    )
    from .packs.quality.q001_select_star import Q001SelectStarRule
    from .packs.quality.q002_missing_pk import Q002MissingPkRule
    from .packs.quality.q003_high_complexity import Q003HighComplexityRule
    from .packs.quality.q004_missing_description import Q004MissingDescriptionRule
    from .packs.quality.q005_undocumented_column import Q005UndocumentedColumnRule
    from .packs.quality.q006_naming_convention import Q006NamingConventionRule
    from .packs.quality.q007_inconsistent_casing import Q007InconsistentCasingRule
    from .packs.security.g001_missing_owner import G001MissingOwnerRule
    from .packs.security.s001_pii_unmasked import S001PiiUnmaskedRule
    from .packs.security.s002_hardcoded_secret import S002HardcodedSecretRule
    from .packs.refactor.r002_god_model import R002GodModelRule
    from .packs.refactor.r003_single_use_cte import R003SingleUseCteRule
    from .packs.refactor.r004_dead_cte import R004DeadCteRule
    from .packs.refactor.r005_duplicate_expression import R005DuplicateExpressionRule
    from .packs.refactor.r006_duplicate_case import R006DuplicateCaseRule
    from .packs.testing.t001_unexecuted_test import T001UnexecutedTestRule
    from .packs.testing.t002_no_unit_tests import T002NoUnitTestsRule
    from .packs.testing.t003_malformed_unit_test import T003MalformedUnitTestRule

    return [
        Q001SelectStarRule,
        Q002MissingPkRule,
        Q003HighComplexityRule,
        Q004MissingDescriptionRule,
        Q005UndocumentedColumnRule,
        Q006NamingConventionRule,
        Q007InconsistentCasingRule,
        P001CrossJoinRule,
        P002NonSargableRule,
        P003SelfJoinInequalityRule,
        P004UnboundedWindowRule,
        P005CountDistinctOverRule,
        P006FanOutJoinRule,
        P007OrderByNoLimitRule,
        P008DeepCteChainRule,
        P009OverReferencedViewRule,
        P010IncrementalMissingKeyRule,
        R001DuplicateModelsRule,
        R002GodModelRule,
        R003SingleUseCteRule,
        R004DeadCteRule,
        R005DuplicateExpressionRule,
        R006DuplicateCaseRule,
        A001LayerViolationRule,
        A002FanInRule,
        A003DirectSourceBypassRule,
        A004CircularDepRule,
        A005LeakyAbstractionRule,
        T001UnexecutedTestRule,
        T002NoUnitTestsRule,
        T003MalformedUnitTestRule,
        S001PiiUnmaskedRule,
        S002HardcodedSecretRule,
        G001MissingOwnerRule,
    ]


def discover_rules() -> list[type]:
    """Built-ins + third-party via ``dbt_coverage.rules`` entry-point group."""
    found: dict[str, type] = {}

    for cls in _builtin_rule_classes():
        rid = getattr(cls, "id", None)
        if not rid:
            continue
        if rid in found:
            raise ConfigError(f"Duplicate rule id: {rid!r}")
        found[rid] = cls

    try:
        eps = entry_points(group="dbt_coverage.rules")
    except TypeError:  # older importlib API
        eps = entry_points().get("dbt_coverage.rules", [])  # type: ignore[assignment]
    except Exception as e:
        _LOG.debug("entry_points lookup failed: %s", e)
        eps = []  # type: ignore[assignment]

    for ep in eps:
        try:
            cls = ep.load()
        except Exception as e:
            _LOG.warning("Failed to load rule plugin %s: %s", ep.name, e)
            continue
        rid = getattr(cls, "id", None)
        if not rid:
            _LOG.warning("Plugin rule %s has no id, skipped", ep.name)
            continue
        if rid in found:
            raise ConfigError(f"Duplicate rule id across plugins: {rid!r}")
        found[rid] = cls

    return list(found.values())


def apply_overrides(
    rule_classes: list[type],
    config: DbtcovConfig,
) -> list[RegisteredRule]:
    """Merge per-rule overrides from config onto class defaults."""
    result: list[RegisteredRule] = []
    known_ids = {getattr(c, "id", "") for c in rule_classes}

    for ov_id in config.rules:
        if ov_id not in known_ids:
            _LOG.warning("Config references unknown rule id %r — ignoring", ov_id)

    for cls in rule_classes:
        rid = getattr(cls, "id", "")
        override = config.rules.get(rid)

        enabled = True
        eff_sev = getattr(cls, "default_severity", Severity.MAJOR)
        eff_tier = getattr(cls, "default_tier", Tier.TIER_2_WARN)
        eff_conf_min = 0.0
        params: dict[str, Any] = {}

        if override is not None:
            enabled = override.enabled
            if override.severity is not None:
                eff_sev = override.severity
            if override.tier is not None:
                eff_tier = override.tier
            if override.confidence_min is not None:
                eff_conf_min = float(override.confidence_min)
            params = dict(override.params)

        # SPEC-20 — inject project-level complexity config as Q003 defaults so
        # users don't have to duplicate thresholds under rules.Q003.params.
        if rid == "Q003":
            ccfg = config.complexity
            base: dict[str, Any] = {
                "threshold_warn": ccfg.threshold_warn,
                "threshold_block": ccfg.threshold_block,
                "include_jinja": ccfg.include_jinja,
                "exempt_models": list(ccfg.exempt_models),
            }
            base.update(params)  # per-rule override wins
            params = base

        # SPEC-32 §7 — inject testing.unit_tests.exempt globs into T002 params.
        if rid == "T002":
            base_t002: dict[str, Any] = {
                "exempt": list(config.testing.unit_tests.exempt),
            }
            base_t002.update(params)
            params = base_t002

        # SPEC-27 §2 — architecture rules share the full ArchitectureConfig via
        # the reserved ``_architecture`` param key. Individual rules still
        # accept their own tuning knobs (e.g. A002's ``threshold``).
        if rid in {"A001", "A003", "A005", "Q006"}:
            base_arch: dict[str, Any] = {"_architecture": config.architecture}
            base_arch.update(params)
            params = base_arch

        result.append(
            RegisteredRule(
                rule_cls=cls,
                enabled=enabled,
                effective_severity=eff_sev,
                effective_tier=eff_tier,
                effective_confidence_min=eff_conf_min,
                params=params,
            )
        )
    return result
