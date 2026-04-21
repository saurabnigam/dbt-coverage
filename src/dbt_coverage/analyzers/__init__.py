"""SPEC-07 — rule engine + registry + base types (public API)."""

from .normalization import extract_code_context, normalize_snippet
from .rule_base import BaseRule, Rule, RuleContext
from .rule_engine import Engine, EngineResult
from .rule_registry import RegisteredRule, apply_overrides, discover_rules
from .waivers import (
    BaselineEntry,
    BaselineFile,
    WaiverResolver,
    capture_baseline,
    load_baseline_for,
)

__all__ = [
    "BaseRule",
    "Rule",
    "RuleContext",
    "Engine",
    "EngineResult",
    "RegisteredRule",
    "apply_overrides",
    "discover_rules",
    "extract_code_context",
    "normalize_snippet",
    "WaiverResolver",
    "BaselineFile",
    "BaselineEntry",
    "load_baseline_for",
    "capture_baseline",
]
