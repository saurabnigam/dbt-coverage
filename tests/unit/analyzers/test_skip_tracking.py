"""SPEC-33 §7 — Engine skip-tracking tests.

Covers the six ``CheckSkipReason`` values that the engine can emit without
requiring a full project-scan context:

* ``RULE_DISABLED``     — ``RegisteredRule.enabled=False``
* ``MODE_REQUIRED``     — rule declares ``required_render_mode`` that differs
                          from the active render mode
* ``ADAPTER_MISSING``   — rule declares ``required_adapter`` not present
* ``ADAPTER_FAILED``    — adapter present but its ``invocation.status`` ≠ "ok"
* ``PARSE_FAILED``      — node's ``parse_success=False`` and rule needs AST
* ``RULE_ERROR``        — rule raises inside ``check()``
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbt_coverage.analyzers import Engine, RegisteredRule
from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import (
    Category,
    CheckSkipReason,
    Finding,
    FindingType,
    ParsedNode,
    RenderMode,
    Severity,
    Tier,
)
from dbt_coverage.scanners import ProjectIndex


# ----------------------------------------------------------------------- helpers


def _node(nid: str = "model.demo.a", *, parse_success: bool = True) -> ParsedNode:
    return ParsedNode(
        file_path=Path(f"models/{nid.split('.')[-1]}.sql"),
        node_id=nid,
        source_sql="select 1",
        rendered_sql="select 1",
        render_mode=RenderMode.MOCK,
        parse_success=parse_success,
    )


def _project() -> ProjectIndex:
    return ProjectIndex(project_root=Path("."), project_name="demo")


def _graph():
    # Engine only passes the graph through; no methods are invoked during
    # these tests, so a dummy namespace keeps the setup lightweight.
    return SimpleNamespace()


def _registered(cls: type, *, enabled: bool = True) -> RegisteredRule:
    return RegisteredRule(
        rule_cls=cls,
        enabled=enabled,
        effective_severity=getattr(cls, "default_severity", Severity.MAJOR),
        effective_tier=getattr(cls, "default_tier", Tier.TIER_2_WARN),
        effective_confidence_min=0.0,
        params={},
    )


class _NoopRule(BaseRule):
    id = "X001"
    description = "noop"
    requires_ast = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        return []


class _CompiledOnlyRule(BaseRule):
    id = "X002"
    description = "requires compiled"
    required_render_mode = "COMPILED"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        return []


class _NeedsDbtTestRule(BaseRule):
    id = "X003"
    description = "requires dbt-test adapter"
    required_adapter = "dbt-test"
    applies_to_node = False  # project-level

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        return []


class _BoomRule(BaseRule):
    id = "X004"
    description = "raises"
    requires_ast = False
    applies_to_node = False  # project-level so the rule always fires once

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        raise RuntimeError("boom")


# ------------------------------------------------------------------------ tests


def test_rule_disabled_emits_skip_and_no_dispatch() -> None:
    rr = _registered(_NoopRule, enabled=False)
    eng = Engine([rr], _graph(), _project(), render_mode="MOCK")

    res = eng.run_with_skips({"model.demo.a": _node()})

    assert res.findings == []
    assert len(res.skips) == 1
    assert res.skips[0].reason is CheckSkipReason.RULE_DISABLED
    assert res.skips[0].rule_id == "X001"
    # Disabled rules are gated *before* node-loop so attempted must stay 0.
    assert res.attempted == 0


def test_mode_required_mismatch_emits_skip() -> None:
    rr = _registered(_CompiledOnlyRule)
    eng = Engine([rr], _graph(), _project(), render_mode="MOCK")

    res = eng.run_with_skips({"model.demo.a": _node()})

    assert res.findings == []
    assert any(s.reason is CheckSkipReason.MODE_REQUIRED for s in res.skips)
    # MODE_REQUIRED is a rule-level gate → attempted stays 0.
    assert res.attempted == 0


def test_mode_required_match_allows_dispatch() -> None:
    rr = _registered(_CompiledOnlyRule)
    eng = Engine([rr], _graph(), _project(), render_mode="COMPILED")

    res = eng.run_with_skips({"model.demo.a": _node()})

    # No MODE_REQUIRED skip; the rule ran over the single node.
    assert not any(s.reason is CheckSkipReason.MODE_REQUIRED for s in res.skips)
    assert res.attempted == 1


def test_adapter_missing_emits_skip() -> None:
    rr = _registered(_NeedsDbtTestRule)
    eng = Engine([rr], _graph(), _project(), adapter_results={})

    res = eng.run_with_skips({})

    assert len(res.skips) == 1
    assert res.skips[0].reason is CheckSkipReason.ADAPTER_MISSING
    assert "dbt-test" in (res.skips[0].details or "")


def test_adapter_failed_emits_skip() -> None:
    rr = _registered(_NeedsDbtTestRule)
    adapter_res = SimpleNamespace(
        invocation=SimpleNamespace(status="error"),
    )
    eng = Engine(
        [rr], _graph(), _project(), adapter_results={"dbt-test": adapter_res}
    )

    res = eng.run_with_skips({})

    assert len(res.skips) == 1
    assert res.skips[0].reason is CheckSkipReason.ADAPTER_FAILED


def test_parse_failed_is_per_node_skip() -> None:
    rr = _registered(_NoopRule)
    eng = Engine([rr], _graph(), _project(), render_mode="MOCK")

    nodes = {
        "model.demo.ok": _node("model.demo.ok"),
        "model.demo.bad": _node("model.demo.bad", parse_success=False),
    }
    res = eng.run_with_skips(nodes)

    per_node = {s.node_id: s.reason for s in res.skips}
    assert per_node == {"model.demo.bad": CheckSkipReason.PARSE_FAILED}
    # Every node counts towards ``attempted`` even when short-circuited.
    assert res.attempted == 2


def test_rule_error_emits_skip_and_internal_crash_finding() -> None:
    rr = _registered(_BoomRule)
    eng = Engine([rr], _graph(), _project(), render_mode="MOCK")

    res = eng.run_with_skips({})

    assert any(s.reason is CheckSkipReason.RULE_ERROR for s in res.skips)
    # Engine also records an INTERNAL_CRASH finding so the user sees it in
    # reports even when the skip is hidden by default console.
    assert any(f.rule_id == "INTERNAL_CRASH" for f in res.findings)


def test_back_compat_run_returns_only_findings() -> None:
    rr = _registered(_NoopRule, enabled=False)
    eng = Engine([rr], _graph(), _project())
    out = eng.run({"model.demo.a": _node()})
    assert out == []


# --------------------------------------------------- orchestrator skip-report shape


def test_build_skip_report_aggregates_by_rule_and_reason() -> None:
    from dbt_coverage.cli.orchestrator import _build_skip_report
    from dbt_coverage.core import CheckSkip
    from dbt_coverage.utils import DbtcovConfig

    skips = [
        CheckSkip(rule_id="X001", reason=CheckSkipReason.PARSE_FAILED, node_id="a"),
        CheckSkip(rule_id="X001", reason=CheckSkipReason.PARSE_FAILED, node_id="b"),
        CheckSkip(rule_id="X002", reason=CheckSkipReason.MODE_REQUIRED),
    ]
    summary, aggregated, per_pair = _build_skip_report(skips, attempted=10, config=DbtcovConfig())

    assert summary.total_skips == 3
    assert summary.attempted_checks == 10
    # Aggregated rolls up by (rule, reason).
    keys = {(a.rule_id, a.reason) for a in aggregated}
    assert keys == {
        ("X001", CheckSkipReason.PARSE_FAILED),
        ("X002", CheckSkipReason.MODE_REQUIRED),
    }
    x001 = next(a for a in aggregated if a.rule_id == "X001")
    assert x001.count == 2
    # ``per_pair`` depends on config.reports.skip_detail default ("aggregated"),
    # so the list should be empty for the default build.
    assert per_pair == [] or len(per_pair) == len(skips)


@pytest.mark.parametrize(
    "detail,expect_per_pair",
    [("summary", False), ("aggregated", False), ("per_pair", True)],
)
def test_build_skip_report_honours_skip_detail(detail: str, expect_per_pair: bool) -> None:
    from dbt_coverage.cli.orchestrator import _build_skip_report
    from dbt_coverage.core import CheckSkip
    from dbt_coverage.utils import DbtcovConfig

    cfg = DbtcovConfig()
    cfg.reports.skip_detail = detail
    skips = [CheckSkip(rule_id="X001", reason=CheckSkipReason.PARSE_FAILED, node_id="a")]

    summary, aggregated, per_pair = _build_skip_report(skips, attempted=1, config=cfg)

    assert summary.total_skips == 1
    # Aggregated is always built (summary still needs per-rule rollup, cheap).
    assert aggregated and aggregated[0].rule_id == "X001"
    assert bool(per_pair) is expect_per_pair
