"""SPEC-20 — tests for the Q003 high-complexity rule."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.analyzers.packs.quality.q003_high_complexity import (
    Q003HighComplexityRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import ComplexityMetrics, ParsedNode, RenderMode, Severity, Tier


def _node() -> ParsedNode:
    return ParsedNode(
        file_path=Path("models/big.sql"),
        node_id="model.demo.big",
        source_sql="select 1",
        rendered_sql="select 1",
        render_mode=RenderMode.MOCK,
    )


def _ctx(complexity: dict[str, ComplexityMetrics], params: dict) -> RuleContext:
    return RuleContext(
        node=_node(),
        node_id="model.demo.big",
        graph=None,  # type: ignore[arg-type]
        project=None,  # type: ignore[arg-type]
        artifacts=None,
        params=params,
        confidence_min=0.0,
        complexity=complexity,
        test_results=[],
    )


def test_q003_no_finding_under_warn() -> None:
    rule = Q003HighComplexityRule()
    ctx = _ctx(
        {"model.demo.big": ComplexityMetrics(cc=10)},
        {"threshold_warn": 15, "threshold_block": 30, "include_jinja": True, "exempt_models": []},
    )
    assert list(rule.check(ctx)) == []


def test_q003_warn_tier_2() -> None:
    rule = Q003HighComplexityRule()
    ctx = _ctx(
        {"model.demo.big": ComplexityMetrics(cc=20)},
        {"threshold_warn": 15, "threshold_block": 30, "include_jinja": True, "exempt_models": []},
    )
    findings = list(rule.check(ctx))
    assert len(findings) == 1
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].tier is Tier.TIER_2_WARN


def test_q003_block_tier_1() -> None:
    rule = Q003HighComplexityRule()
    ctx = _ctx(
        {"model.demo.big": ComplexityMetrics(cc=40)},
        {"threshold_warn": 15, "threshold_block": 30, "include_jinja": True, "exempt_models": []},
    )
    findings = list(rule.check(ctx))
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].tier is Tier.TIER_1_ENFORCED


def test_q003_exempt_models() -> None:
    rule = Q003HighComplexityRule()
    ctx = _ctx(
        {"model.demo.big": ComplexityMetrics(cc=50)},
        {
            "threshold_warn": 15,
            "threshold_block": 30,
            "include_jinja": True,
            "exempt_models": ["model.demo.*"],
        },
    )
    assert list(rule.check(ctx)) == []


def test_q003_include_jinja_false_subtracts_jinja_contribution() -> None:
    rule = Q003HighComplexityRule()
    metrics = ComplexityMetrics(cc=20, jinja_ifs=8, jinja_fors=2)
    # With jinja excluded, cc = 20 - 10 = 10, under warn=15 → no finding.
    ctx = _ctx(
        {"model.demo.big": metrics},
        {
            "threshold_warn": 15,
            "threshold_block": 30,
            "include_jinja": False,
            "exempt_models": [],
        },
    )
    assert list(rule.check(ctx)) == []


def test_q003_no_complexity_entry_noop() -> None:
    rule = Q003HighComplexityRule()
    ctx = _ctx(
        {},
        {"threshold_warn": 15, "threshold_block": 30, "include_jinja": True, "exempt_models": []},
    )
    assert list(rule.check(ctx)) == []
