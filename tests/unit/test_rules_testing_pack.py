"""SPEC-32 §6 — unit tests for T001, T002, T003."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.analyzers.packs.testing.t001_unexecuted_test import (
    T001UnexecutedTestRule,
)
from dbt_coverage.analyzers.packs.testing.t002_no_unit_tests import (
    T002NoUnitTestsRule,
)
from dbt_coverage.analyzers.packs.testing.t003_malformed_unit_test import (
    T003MalformedUnitTestRule,
)
from dbt_coverage.analyzers.rule_base import RuleContext
from dbt_coverage.core import (
    ParsedNode,
    RenderMode,
    Severity,
    TestKind,
    TestResult,
    TestStatus,
    Tier,
)


def _model_node(nid: str, path: str = "models/marts/fct_orders.sql") -> ParsedNode:
    return ParsedNode(
        file_path=Path(path),
        node_id=nid,
        source_sql="select 1",
        rendered_sql="select 1",
        render_mode=RenderMode.MOCK,
    )


def _ctx(
    *,
    node: ParsedNode | None = None,
    node_id: str | None = None,
    test_results: list[TestResult] | None = None,
    params: dict | None = None,
    dbt_version: str | None = "1.8.3",
) -> RuleContext:
    return RuleContext(
        node=node,
        node_id=node_id,
        graph=None,  # type: ignore[arg-type]
        project=None,  # type: ignore[arg-type]
        artifacts=None,
        params=params or {},
        confidence_min=0.0,
        test_results=test_results or [],
        dbt_version=dbt_version,
    )


# ---------------------------------------------------------------- T001 --


def _tr(
    name: str,
    *,
    executed: bool,
    model_uid: str | None = "model.demo.fct_orders",
    kind: TestKind = TestKind.DATA,
    status: TestStatus = TestStatus.UNKNOWN,
    file_path: Path | None = Path("models/staging/stg_orders.yml"),
    malformed: str | None = None,
) -> TestResult:
    return TestResult(
        test_name=name,
        test_kind="not_null" if kind is TestKind.DATA else "unit_test",
        model_unique_id=model_uid,
        status=status,
        origin="dbt-test",
        kind=kind,
        executed=executed,
        file_path=file_path,
        malformed_reason=malformed,
    )


def test_t001_fires_once_per_unexecuted_test() -> None:
    rule = T001UnexecutedTestRule()
    results = [
        _tr("not_null_id", executed=False),
        _tr("unique_id", executed=True, status=TestStatus.PASS),
        _tr("check_revenue", executed=False, kind=TestKind.UNIT),
    ]
    findings = list(rule.check(_ctx(test_results=results)))
    assert len(findings) == 2
    ids = {f.message.split("`")[1] for f in findings}
    assert ids == {"not_null_id", "check_revenue"}
    assert all(f.tier is Tier.TIER_1_ENFORCED for f in findings)
    assert all(f.severity is Severity.MAJOR for f in findings)
    assert all(f.rule_id == "T001" for f in findings)


def test_t001_silent_when_everything_executed() -> None:
    rule = T001UnexecutedTestRule()
    results = [_tr("not_null_id", executed=True, status=TestStatus.PASS)]
    assert list(rule.check(_ctx(test_results=results))) == []


# ---------------------------------------------------------------- T002 --


def test_t002_fires_when_model_has_no_unit_tests() -> None:
    rule = T002NoUnitTestsRule()
    node = _model_node("model.demo.fct_orders")
    results = [_tr("not_null_id", executed=True, kind=TestKind.DATA)]
    findings = list(
        rule.check(_ctx(node=node, node_id=node.node_id, test_results=results))
    )
    assert len(findings) == 1
    assert findings[0].rule_id == "T002"
    assert findings[0].severity is Severity.MINOR
    assert findings[0].tier is Tier.TIER_2_WARN


def test_t002_silent_when_unit_test_present() -> None:
    rule = T002NoUnitTestsRule()
    node = _model_node("model.demo.fct_orders")
    results = [
        _tr(
            "check_revenue",
            executed=True,
            kind=TestKind.UNIT,
            model_uid="model.demo.fct_orders",
        )
    ]
    assert list(rule.check(_ctx(node=node, node_id=node.node_id, test_results=results))) == []


def test_t002_skips_staging_models() -> None:
    rule = T002NoUnitTestsRule()
    node = _model_node("model.demo.stg_orders", path="models/staging/stg_orders.sql")
    assert list(rule.check(_ctx(node=node, node_id=node.node_id))) == []


def test_t002_skips_dbt_below_1_8() -> None:
    rule = T002NoUnitTestsRule()
    node = _model_node("model.demo.fct_orders")
    ctx = _ctx(node=node, node_id=node.node_id, dbt_version="1.7.12")
    assert list(rule.check(ctx)) == []


def test_t002_respects_exempt_globs() -> None:
    rule = T002NoUnitTestsRule()
    node = _model_node("model.demo.seeds_copy", path="models/seeds/seed_x.sql")
    ctx = _ctx(
        node=node,
        node_id=node.node_id,
        params={"exempt": ["models/seeds/*"]},
    )
    assert list(rule.check(ctx)) == []


# ---------------------------------------------------------------- T003 --


def test_t003_fires_on_malformed_unit_test() -> None:
    rule = T003MalformedUnitTestRule()
    results = [
        _tr(
            "broken_unit",
            executed=True,
            kind=TestKind.UNIT,
            malformed="missing `given` block",
        ),
        _tr("healthy_unit", executed=True, kind=TestKind.UNIT),
        _tr("not_null_id", executed=True, kind=TestKind.DATA, malformed="ignored"),
    ]
    findings = list(rule.check(_ctx(test_results=results)))
    assert len(findings) == 1
    assert findings[0].rule_id == "T003"
    assert "missing `given` block" in findings[0].message
    assert findings[0].severity is Severity.MAJOR
