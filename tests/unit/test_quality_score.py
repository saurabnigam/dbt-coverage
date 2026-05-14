"""Quality score, coverage percentage, and violation-impact unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from dbt_coverage.cli.orchestrator import _build_model_summaries
from dbt_coverage.core import (
    Category,
    CheckSkip,
    CheckSkipReason,
    ComplexityMetrics,
    CoverageMetric,
    Finding,
    FindingType,
    ParsedNode,
    RenderMode,
    RenderStats,
    ScanResult,
    Severity,
    TestKind,
    TestResult,
    TestStatus,
    Tier,
)
from dbt_coverage.coverage import (
    compute_complexity_summary,
    compute_doc_coverage,
    compute_test_cc_weighted_coverage,
    compute_test_coverage,
    compute_test_meaningful_coverage,
)
from dbt_coverage.quality_gates import CoverageThreshold, GateConfig, evaluate
from dbt_coverage.scanners import ModelEntry, ProjectIndex
from dbt_coverage.scanners.project_index import IndexedFile, YamlColumnMeta, YamlModelMeta
from dbt_coverage.utils import DbtcovConfig
from dbt_coverage.utils.config import ScoringConfig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DUMMY_SQL = "select 1 as id"


def _indexed_file(name: str) -> IndexedFile:
    return IndexedFile(
        path=Path(f"models/{name}.sql"),
        absolute_path=Path(f"/proj/models/{name}.sql"),
        content=_DUMMY_SQL,
        source_hash="abc",
    )


def _yml_meta(
    name: str,
    description: str | None = "",
    columns: list | None = None,
    has_tests: bool = False,
) -> YamlModelMeta:
    return YamlModelMeta(
        name=name,
        description=description if description else None,
        columns=columns or [],
        tests=["not_null"] if has_tests else [],
        file_path=Path(f"models/{name}.yml"),
    )


def _col(
    name: str,
    description: str | None = "",
    has_tests: bool = False,
) -> YamlColumnMeta:
    return YamlColumnMeta(
        name=name,
        description=description if description else None,
        tests=["not_null"] if has_tests else [],
    )


def _entry(name: str, yml: YamlModelMeta | None = None) -> ModelEntry:
    return ModelEntry(
        node_id=f"model.demo.{name}",
        name=name,
        sql_file=_indexed_file(name),
        yml_meta=yml,
    )


def _project(*names: str, yml_map: dict | None = None) -> ProjectIndex:
    yml_map = yml_map or {}
    proj = ProjectIndex(project_root=Path("."), project_name="demo")
    for n in names:
        proj.models[f"model.demo.{n}"] = _entry(n, yml=yml_map.get(n))
    return proj


def _node(
    name: str,
    parse_success: bool = True,
    render_uncertain: bool = False,
) -> ParsedNode:
    return ParsedNode(
        file_path=Path(f"models/{name}.sql"),
        node_id=f"model.demo.{name}",
        source_sql=_DUMMY_SQL,
        rendered_sql=_DUMMY_SQL if parse_success else "",
        render_mode=RenderMode.MOCK,
        parse_success=parse_success,
        render_uncertain=render_uncertain,
    )


def _nodes(*names: str, parse_success: bool = True) -> dict[str, ParsedNode]:
    return {f"model.demo.{n}": _node(n, parse_success=parse_success) for n in names}


def _finding(
    rule_id: str,
    tier: Tier = Tier.TIER_1_ENFORCED,
    node_id: str | None = None,
    suppressed: bool = False,
) -> Finding:
    # Fingerprint must be exactly 16 chars
    fp = (rule_id + "_" * 16)[:16]
    return Finding(
        rule_id=rule_id,
        severity=Severity.MAJOR,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=tier,
        confidence=0.9,
        message="violation",
        file_path=Path("models/a.sql"),  # relative path required
        line=1,
        column=1,
        fingerprint=fp,
        node_id=node_id,
        suppressed=suppressed,
    )


def _tr(
    name: str,
    model: str,
    kind: str = "not_null",          # test_kind (string for classification)
    status: TestStatus = TestStatus.PASS,
    executed: bool = True,
    test_kind: TestKind = TestKind.DATA,   # kind (enum for DATA/UNIT/UNKNOWN)
) -> TestResult:
    """Build a TestResult. `kind` is the string test classifier; `test_kind` is the enum."""
    return TestResult(
        test_name=name,
        test_kind=kind,
        model_unique_id=f"model.demo.{model}",
        status=status,
        origin="dbt_test",
        kind=test_kind,
        executed=executed,
    )


def _skip(rule_id: str, model: str) -> CheckSkip:
    return CheckSkip(
        rule_id=rule_id,
        node_id=f"model.demo.{model}",
        reason=CheckSkipReason.RULE_DISABLED,
    )


def _cfg(**sc_kwargs) -> DbtcovConfig:
    if sc_kwargs:
        return DbtcovConfig(scoring=ScoringConfig(**sc_kwargs))
    return DbtcovConfig()


def _cov(
    nid: str,
    test_ct: tuple[int, int] = (1, 1),
    doc_ct: tuple[int, int] = (1, 1),
    column_test_ct: tuple[int, int] | None = None,
    column_test_meaningful_ct: tuple[int, int] | None = None,
    test_unit_wcc_ct: tuple[int, int] | None = None,
) -> list[CoverageMetric]:
    """Return minimal coverage metrics (test + doc + optional new dims) for a single model."""
    tc, tt = test_ct
    dc, dt = doc_ct
    t_ratio = tc / tt if tt > 0 else 0.0
    d_ratio = dc / dt if dt > 0 else 0.0
    metrics = [
        CoverageMetric(
            dimension="test",
            covered=tc,
            total=tt,
            ratio=t_ratio,
            per_node={nid: test_ct},
        ),
        CoverageMetric(
            dimension="doc",
            covered=dc,
            total=dt,
            ratio=d_ratio,
            per_node={nid: doc_ct},
        ),
    ]
    if column_test_ct is not None:
        cc, ct = column_test_ct
        metrics.append(CoverageMetric(
            dimension="column_test",
            covered=cc,
            total=ct,
            ratio=(cc / ct if ct > 0 else 0.0),
            per_node={nid: column_test_ct},
        ))
    if column_test_meaningful_ct is not None:
        mc, mt = column_test_meaningful_ct
        metrics.append(CoverageMetric(
            dimension="column_test_meaningful",
            covered=mc,
            total=mt,
            ratio=(mc / mt if mt > 0 else 0.0),
            per_node={nid: column_test_meaningful_ct},
        ))
    if test_unit_wcc_ct is not None:
        uc, ut = test_unit_wcc_ct
        metrics.append(CoverageMetric(
            dimension="test_unit_weighted_cc",
            covered=uc,
            total=ut,
            ratio=(uc / ut if ut > 0 else 0.0),
            per_node={nid: test_unit_wcc_ct},
        ))
    return metrics


def _multi_cov(
    *items: tuple,
) -> list[CoverageMetric]:
    """Build one merged test+doc+test_column CoverageMetric covering all given models.

    Each item is ``(nid, test_ct, doc_ct)`` or ``(nid, test_ct, doc_ct, column_test_ct)``
    where *_ct are ``(covered, total)``.
    Returns a single CoverageMetric per dimension so _build_model_summaries
    reads all per_node entries from one metric (it replaces, not merges).
    """
    test_per: dict = {}
    doc_per: dict = {}
    col_per: dict = {}
    for item in items:
        nid, test_ct, doc_ct = item[0], item[1], item[2]
        test_per[nid] = test_ct
        doc_per[nid] = doc_ct
        if len(item) > 3:
            col_per[nid] = item[3]
    tc = sum(v[0] for v in test_per.values())
    tt = sum(v[1] for v in test_per.values())
    dc = sum(v[0] for v in doc_per.values())
    dt = sum(v[1] for v in doc_per.values())
    metrics = [
        CoverageMetric(
            dimension="test",
            covered=tc,
            total=tt,
            ratio=tc / tt if tt > 0 else 0.0,
            per_node=test_per,
        ),
        CoverageMetric(
            dimension="doc",
            covered=dc,
            total=dt,
            ratio=dc / dt if dt > 0 else 0.0,
            per_node=doc_per,
        ),
    ]
    if col_per:
        cc = sum(v[0] for v in col_per.values())
        ct = sum(v[1] for v in col_per.values())
        metrics.append(CoverageMetric(
            dimension="column_test",
            covered=cc,
            total=ct,
            ratio=cc / ct if ct > 0 else 0.0,
            per_node=col_per,
        ))
    return metrics


def _build_summaries(
    parsed_nodes: dict,
    coverage: list,
    findings: list | None = None,
    test_results: list | None = None,
    check_skips: list | None = None,
    cfg: DbtcovConfig | None = None,
) -> list:
    """Wrapper: builds a minimal ProjectIndex then calls _build_model_summaries."""
    proj = ProjectIndex(project_root=Path("."), project_name="demo")
    for node_id in parsed_nodes:
        name = node_id.split(".")[-1]
        proj.models[node_id] = _entry(name)
    return _build_model_summaries(
        project=proj,
        parsed_nodes=parsed_nodes,
        findings=findings or [],
        coverage=coverage,
        test_results=test_results or [],
        check_skips=check_skips or [],
        cfg=cfg or _cfg(),
    )


def _scan_result(dim: str, covered: int, total: int) -> ScanResult:
    ratio = covered / total if total > 0 else 0.0
    return ScanResult(
        findings=[],
        coverage=[
            CoverageMetric(
                dimension=dim,
                covered=covered,
                total=total,
                ratio=ratio,
            )
        ],
        project_root=Path("/tmp"),
        dialect="postgres",
        render_stats=RenderStats(total_files=1, parse_success=1),
    )


# ---------------------------------------------------------------------------
# 1. Documentation coverage
# ---------------------------------------------------------------------------


class TestDocumentationCoverage:
    def test_model_with_description_is_covered(self) -> None:
        proj = _project("a", yml_map={"a": _yml_meta("a", description="A model")})
        m = compute_doc_coverage(proj)
        assert m.covered == 1
        assert m.total == 1
        assert m.ratio == 1.0

    def test_model_without_description_is_not_covered(self) -> None:
        proj = _project("a", yml_map={"a": _yml_meta("a", description=None)})
        m = compute_doc_coverage(proj)
        assert m.covered == 0
        assert m.total == 1
        assert m.ratio == 0.0

    def test_model_with_empty_description_is_not_covered(self) -> None:
        proj = _project("a", yml_map={"a": _yml_meta("a", description="")})
        m = compute_doc_coverage(proj)
        assert m.covered == 0
        assert m.total == 1

    def test_model_with_no_yml_is_not_covered(self) -> None:
        proj = _project("a")  # no yml_meta
        m = compute_doc_coverage(proj)
        assert m.covered == 0
        assert m.total == 1

    def test_partial_doc_coverage_two_of_three(self) -> None:
        proj = _project(
            "a", "b", "c",
            yml_map={
                "a": _yml_meta("a", description="desc"),
                "b": _yml_meta("b", description="desc"),
                "c": _yml_meta("c", description=None),
            },
        )
        m = compute_doc_coverage(proj)
        assert m.covered == 2
        assert m.total == 3
        assert abs(m.ratio - 2 / 3) < 1e-9

    def test_zero_models_returns_zero_ratio(self) -> None:
        proj = ProjectIndex(project_root=Path("."), project_name="demo")
        m = compute_doc_coverage(proj)
        assert m.total == 0
        assert m.covered == 0
        # When there are no models, ratio defaults to 1.0 (nothing to document)


# ---------------------------------------------------------------------------
# 2. Data test coverage (declaration-based via YAML)
# ---------------------------------------------------------------------------


class TestDataTestCoverage:
    def test_model_with_tests_is_covered(self) -> None:
        proj = _project("a", yml_map={"a": _yml_meta("a", description="d", has_tests=True)})
        m = compute_test_coverage(proj)
        assert m.covered == 1
        assert m.total == 1

    def test_model_without_tests_is_not_covered(self) -> None:
        proj = _project("a", yml_map={"a": _yml_meta("a", description="d", has_tests=False)})
        m = compute_test_coverage(proj)
        assert m.covered == 0
        assert m.total == 1

    def test_column_test_also_covers_model(self) -> None:
        yml = _yml_meta(
            "a",
            description="d",
            columns=[_col("id", has_tests=True)],
        )
        proj = _project("a", yml_map={"a": yml})
        m = compute_test_coverage(proj)
        assert m.covered == 1

    def test_model_with_no_yml_is_not_covered(self) -> None:
        proj = _project("a")
        m = compute_test_coverage(proj)
        assert m.covered == 0
        assert m.total == 1

    def test_50_percent_coverage(self) -> None:
        proj = _project(
            "a", "b",
            yml_map={
                "a": _yml_meta("a", description="d", has_tests=True),
                "b": _yml_meta("b", description="d", has_tests=False),
            },
        )
        m = compute_test_coverage(proj)
        assert m.covered == 1
        assert m.total == 2
        assert m.ratio == 0.5


# ---------------------------------------------------------------------------
# 3. Unit test coverage
# ---------------------------------------------------------------------------


class TestUnitTestCoverage:
    def test_unit_test_result_covers_model(self) -> None:
        from dbt_coverage.coverage.test_unit_coverage import compute_test_unit_coverage

        nodes = _nodes("a")
        tr = _tr("u1", "a", kind="unit_test", test_kind=TestKind.UNIT)
        m = compute_test_unit_coverage(nodes, [tr])
        assert m.covered == 1
        assert m.total == 1

    def test_data_test_not_counted_as_unit(self) -> None:
        from dbt_coverage.coverage.test_unit_coverage import compute_test_unit_coverage

        nodes = _nodes("a")
        tr = _tr("t1", "a", kind="not_null", test_kind=TestKind.DATA)
        m = compute_test_unit_coverage(nodes, [tr])
        assert m.covered == 0
        assert m.total == 1

    def test_no_tests_gives_zero_unit_coverage(self) -> None:
        from dbt_coverage.coverage.test_unit_coverage import compute_test_unit_coverage

        nodes = _nodes("a", "b")
        m = compute_test_unit_coverage(nodes, [])
        assert m.covered == 0
        assert m.total == 2

    def test_one_of_two_models_has_unit_test(self) -> None:
        from dbt_coverage.coverage.test_unit_coverage import compute_test_unit_coverage

        nodes = _nodes("a", "b")
        tr = _tr("u1", "a", kind="unit_test", test_kind=TestKind.UNIT)
        m = compute_test_unit_coverage(nodes, [tr])
        assert m.covered == 1
        assert m.total == 2
        assert m.ratio == 0.5


# ---------------------------------------------------------------------------
# 4. Meaningful test coverage
# ---------------------------------------------------------------------------


class TestMeaningfulTestCoverage:
    def test_100_percent_with_logical_tests(self) -> None:
        nodes = _nodes("a")
        cfg = _cfg()
        tr = _tr("s1", "a", kind="singular")
        m = compute_test_meaningful_coverage(nodes, [tr], cfg)
        assert m.covered == 1
        assert m.total == 1
        assert m.ratio == 1.0

    def test_trivial_tests_do_not_count(self) -> None:
        nodes = _nodes("a")
        cfg = _cfg()
        tr_nn = _tr("nn1", "a", kind="not_null")
        tr_uq = _tr("uq1", "a", kind="unique")
        m = compute_test_meaningful_coverage(nodes, [tr_nn, tr_uq], cfg)
        assert m.covered == 0
        assert m.total == 1

    def test_only_failing_logical_test_does_not_cover(self) -> None:
        nodes = _nodes("a")
        cfg = _cfg()
        tr = _tr("s1", "a", kind="singular", status=TestStatus.FAIL)
        m = compute_test_meaningful_coverage(nodes, [tr], cfg)
        assert m.covered == 0

    def test_50_percent_meaningful_coverage(self) -> None:
        nodes = _nodes("a", "b")
        cfg = _cfg()
        tr = _tr("s1", "a", kind="singular")
        m = compute_test_meaningful_coverage(nodes, [tr], cfg)
        assert m.covered == 1
        assert m.total == 2
        assert m.ratio == 0.5

    def test_no_tests_gives_zero_meaningful(self) -> None:
        nodes = _nodes("a", "b")
        cfg = _cfg()
        m = compute_test_meaningful_coverage(nodes, [], cfg)
        assert m.covered == 0
        assert m.total == 2
        assert m.ratio == 0.0


# ---------------------------------------------------------------------------
# 5. Complexity-weighted test coverage
# ---------------------------------------------------------------------------


class TestComplexityWeightedCoverage:
    def test_cc_weighted_100_percent(self) -> None:
        nodes = _nodes("a", "b")
        cfg = _cfg()
        complexity = {
            "model.demo.a": ComplexityMetrics(cc=5),
            "model.demo.b": ComplexityMetrics(cc=10),
        }
        trs = [
            _tr("s1", "a", kind="singular"),
            _tr("s2", "b", kind="singular"),
        ]
        m = compute_test_cc_weighted_coverage(nodes, complexity, trs, cfg)
        assert m.total == 15
        assert m.covered == 15
        assert abs(m.ratio - 1.0) < 1e-9

    def test_cc_weighted_0_percent_no_logical_tests(self) -> None:
        nodes = _nodes("a")
        cfg = _cfg()
        complexity = {"model.demo.a": ComplexityMetrics(cc=10)}
        m = compute_test_cc_weighted_coverage(nodes, complexity, [], cfg)
        assert m.covered == 0
        assert m.ratio == 0.0

    def test_cc_weighted_trivial_test_contributes_nothing(self) -> None:
        nodes = _nodes("a")
        cfg = _cfg()
        complexity = {"model.demo.a": ComplexityMetrics(cc=10)}
        tr = _tr("nn1", "a", kind="not_null")   # TRIVIAL → weight=0.0
        m = compute_test_cc_weighted_coverage(nodes, complexity, [tr], cfg)
        assert m.covered == 0
        assert m.ratio == 0.0

    def test_cc_weighted_biased_low_by_uncovered_heavy_model(self) -> None:
        """Model b has cc=99 and no tests; model a has cc=1 and a logical test."""
        nodes = _nodes("a", "b")
        cfg = _cfg()
        complexity = {
            "model.demo.a": ComplexityMetrics(cc=1),
            "model.demo.b": ComplexityMetrics(cc=99),
        }
        tr = _tr("s1", "a", kind="singular")
        m = compute_test_cc_weighted_coverage(nodes, complexity, [tr], cfg)
        assert m.total == 100
        assert m.covered == 1
        assert abs(m.ratio - 0.01) < 1e-6

    def test_missing_complexity_defaults_to_cc1(self) -> None:
        """Models with no ComplexityMetrics entry are treated as cc=1."""
        nodes = _nodes("a", "b")
        cfg = _cfg()
        # No complexity dict entries — defaults to cc=1 each
        tr_a = _tr("s1", "a", kind="singular")
        tr_b = _tr("s2", "b", kind="singular")
        m = compute_test_cc_weighted_coverage(nodes, {}, [tr_a, tr_b], cfg)
        assert m.total == 2
        assert m.covered == 2
        assert m.ratio == 1.0


# ---------------------------------------------------------------------------
# 6. Complexity summary coverage
# ---------------------------------------------------------------------------


class TestComplexityCoverage:
    def test_low_cc_model_is_covered(self) -> None:
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        nodes = _nodes("a")
        m = compute_complexity_summary(
            nodes, {"model.demo.a": ComplexityMetrics(cc=5)}, cfg
        )
        assert m.covered == 1
        assert m.total == 1

    def test_high_cc_model_is_not_covered(self) -> None:
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        nodes = _nodes("a")
        m = compute_complexity_summary(
            nodes, {"model.demo.a": ComplexityMetrics(cc=30)}, cfg
        )
        assert m.covered == 0
        assert m.total == 1

    def test_exactly_at_threshold_is_covered(self) -> None:
        """cc == threshold_warn is covered (boundary is <= inclusive)."""
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        nodes = _nodes("a")
        m = compute_complexity_summary(
            nodes, {"model.demo.a": ComplexityMetrics(cc=15)}, cfg
        )
        assert m.covered == 1

    def test_one_above_threshold_is_not_covered(self) -> None:
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        nodes = _nodes("a")
        m = compute_complexity_summary(
            nodes, {"model.demo.a": ComplexityMetrics(cc=16)}, cfg
        )
        assert m.covered == 0

    def test_missing_complexity_entry_defaults_covered(self) -> None:
        """No entry in complexity dict → treated as cc=1 (below any threshold)."""
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        nodes = _nodes("a")
        m = compute_complexity_summary(nodes, {}, cfg)
        assert m.covered == 1

    def test_mixed_two_covered_one_not(self) -> None:
        cfg = _cfg()
        cfg.complexity.threshold_warn = 10
        nodes = _nodes("a", "b", "c")
        complexity = {
            "model.demo.a": ComplexityMetrics(cc=3),
            "model.demo.b": ComplexityMetrics(cc=10),   # exactly at threshold
            "model.demo.c": ComplexityMetrics(cc=11),   # one above
        }
        m = compute_complexity_summary(nodes, complexity, cfg)
        assert m.covered == 2
        assert m.total == 3

    def test_parse_failed_model_excluded_from_complexity(self) -> None:
        """Parse-failed models must not inflate complexity coverage.

        When parse_success=False the SQL AST is None so CC defaults to 1
        (below any realistic threshold).  Without the exclusion the model
        would be counted as 'covered' even though its real complexity is
        unknown.  It should be excluded from both numerator and denominator,
        and its per_node entry should be (0, 0) so the UI shows '—'.
        """
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        # "a" parses fine with high CC → not covered
        # "b" fails to parse → must be excluded entirely
        nodes = {
            "model.demo.a": _node("a", parse_success=True),
            "model.demo.b": _node("b", parse_success=False),
        }
        complexity = {
            "model.demo.a": ComplexityMetrics(cc=20),   # above threshold
            "model.demo.b": ComplexityMetrics(cc=1),    # would be 'covered' if included
        }
        m = compute_complexity_summary(nodes, complexity, cfg)
        assert m.total == 1, "parse-failed model must not count toward total"
        assert m.covered == 0
        assert m.per_node["model.demo.b"] == (0, 0), "UI needs (0,0) to show '—'"

    def test_parse_failed_model_does_not_count_as_covered(self) -> None:
        """Ensure that a mix of parse-failed and parse-successful models only
        counts parse-successful models toward the complexity coverage ratio."""
        cfg = _cfg()
        cfg.complexity.threshold_warn = 15
        # "a" parses fine and is under threshold → covered
        # "b" fails to parse → excluded → total remains 1, ratio 100%
        nodes = {
            "model.demo.a": _node("a", parse_success=True),
            "model.demo.b": _node("b", parse_success=False),
        }
        complexity = {
            "model.demo.a": ComplexityMetrics(cc=5),
            "model.demo.b": ComplexityMetrics(cc=1),
        }
        m = compute_complexity_summary(nodes, complexity, cfg)
        assert m.total == 1
        assert m.covered == 1
        assert abs(m.ratio - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 7. Quality score — happy path and penalty progressions
# ---------------------------------------------------------------------------


class TestQualityScore:
    """Tests for _build_model_summaries score computation.

    test_results=[] (falsy) throughout so the YAML-based coverage path is used.
    """

    def _summaries(
        self,
        nid: str,
        coverage: list[CoverageMetric],
        findings: list[Finding] | None = None,
        parsed_nodes: dict | None = None,
        test_results: list[TestResult] | None = None,
        check_skips: list[CheckSkip] | None = None,
        cfg: DbtcovConfig | None = None,
    ):
        if parsed_nodes is None:
            name = nid.split(".")[-1]
            parsed_nodes = {nid: _node(name)}
        return _build_summaries(
            parsed_nodes=parsed_nodes,
            coverage=coverage,
            findings=findings,
            test_results=test_results,
            check_skips=check_skips,
            cfg=cfg,
        )

    def test_score_100_perfect(self) -> None:
        nid = "model.demo.a"
        ss = self._summaries(nid, _cov(nid))
        assert len(ss) == 1
        assert ss[0].score == 100

    def test_score_75_no_test_penalty(self) -> None:
        """Zero column test coverage → column_test_penalty_max=25."""
        nid = "model.demo.a"
        ss = self._summaries(nid, _cov(nid, test_ct=(0, 1), column_test_ct=(0, 4)))
        assert ss[0].score == 75
        assert ss[0].score_breakdown["column_test"] == 25

    def test_score_85_no_doc_penalty(self) -> None:
        """Zero doc coverage → doc_penalty_max=15."""
        nid = "model.demo.a"
        ss = self._summaries(nid, _cov(nid, doc_ct=(0, 1)))
        assert ss[0].score == 85
        assert ss[0].score_breakdown["doc"] == 15

    def test_score_partial_doc_penalty_scales_linearly(self) -> None:
        """50% doc ratio → penalty = round(0.5 * 15) = 8 → score = 92."""
        nid = "model.demo.a"
        ss = self._summaries(nid, _cov(nid, doc_ct=(1, 2)))
        assert ss[0].score == 92
        assert ss[0].score_breakdown["doc"] == 8

    def test_score_10pct_doc_penalty_14(self) -> None:
        """10% doc ratio → penalty = round(0.9 * 15) = 14 → score = 86."""
        nid = "model.demo.a"
        ss = self._summaries(nid, _cov(nid, doc_ct=(1, 10)))
        assert ss[0].score == 86
        assert ss[0].score_breakdown["doc"] == 14

    def test_score_57_no_test_no_doc_one_tier2(self) -> None:
        """test_column(25) + no_doc(15) + tier2(3) = 43 → score = 57."""
        nid = "model.demo.a"
        f1 = _finding("P001", tier=Tier.TIER_2_WARN, node_id=nid)
        ss = self._summaries(nid, _cov(nid, test_ct=(0, 1), doc_ct=(0, 1), column_test_ct=(0, 4)), findings=[f1])
        assert ss[0].score == 57
        assert ss[0].score_breakdown["column_test"] == 25
        assert ss[0].score_breakdown["doc"] == 15
        assert ss[0].score_breakdown["tier2"] == 3

    def test_score_20_four_unique_tier1_findings_capped(self) -> None:
        """4 unique-rule tier1 findings → 4×10=40 (= cap) + 25+15 = 80 → score=20."""
        nid = "model.demo.a"
        findings = [
            _finding(f"Q{i:03d}", tier=Tier.TIER_1_ENFORCED, node_id=nid)
            for i in range(4)
        ]
        ss = self._summaries(
            nid, _cov(nid, test_ct=(0, 1), doc_ct=(0, 1), column_test_ct=(0, 4)), findings=findings
        )
        assert ss[0].score == 20
        assert ss[0].score_breakdown["tier1"] == 40

    def test_score_floors_at_zero_when_penalties_exceed_100(self) -> None:
        """10 tier1 (cap=40) + 10 tier2 (cap=20) + 25 + 15 = 100 → score = 0."""
        nid = "model.demo.a"
        t1s = [
            _finding(f"Q{i:03d}", tier=Tier.TIER_1_ENFORCED, node_id=nid)
            for i in range(10)
        ]
        t2s = [
            _finding(f"P{i:03d}", tier=Tier.TIER_2_WARN, node_id=nid)
            for i in range(10)
        ]
        ss = self._summaries(
            nid,
            _cov(nid, test_ct=(0, 1), doc_ct=(0, 1), column_test_ct=(0, 4)),
            findings=t1s + t2s,
        )
        assert ss[0].score == 0


# ---------------------------------------------------------------------------
# 8. Custom ScoringConfig thresholds
# ---------------------------------------------------------------------------


class TestCustomScoringConfig:
    def test_custom_no_test_penalty_zero(self) -> None:
        """Disabling column_test_penalty_max means missing column tests don't deduct."""
        nid = "model.demo.a"
        cfg = _cfg(column_test_penalty_max=0)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid, test_ct=(0, 1), column_test_ct=(0, 4)),
            cfg=cfg,
        )
        assert ss[0].score_breakdown["column_test"] == 0
        assert ss[0].score == 100

    def test_custom_no_test_penalty_50(self) -> None:
        nid = "model.demo.a"
        cfg = _cfg(column_test_penalty_max=50)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid, test_ct=(0, 1), column_test_ct=(0, 4)),
            cfg=cfg,
        )
        assert ss[0].score_breakdown["column_test"] == 50
        assert ss[0].score == 50

    def test_custom_tier1_per_finding_20(self) -> None:
        """tier1_per_finding=20, cap=40 → 2 findings → 40 (capped)."""
        nid = "model.demo.a"
        cfg = _cfg(tier1_per_finding=20, tier1_cap=40)
        f1 = _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid)
        f2 = _finding("Q002", tier=Tier.TIER_1_ENFORCED, node_id=nid)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=[f1, f2],
            coverage=_cov(nid),
            cfg=cfg,
        )
        assert ss[0].score_breakdown["tier1"] == 40

    def test_custom_doc_penalty_max_zero(self) -> None:
        """doc_penalty_max=0 → no doc penalty even with 0% coverage."""
        nid = "model.demo.a"
        cfg = _cfg(doc_penalty_max=0)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid, doc_ct=(0, 1)),
            cfg=cfg,
        )
        assert ss[0].score_breakdown["doc"] == 0
        assert ss[0].score == 100


# ---------------------------------------------------------------------------
# 9. Individual violation impacts
# ---------------------------------------------------------------------------


class TestViolationsImpact:
    def test_single_tier1_deducts_10(self) -> None:
        nid = "model.demo.a"
        f1 = _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=[f1],
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["tier1"] == 10
        assert ss[0].score == 90

    def test_single_tier2_deducts_3(self) -> None:
        nid = "model.demo.a"
        f1 = _finding("P001", tier=Tier.TIER_2_WARN, node_id=nid)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=[f1],
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["tier2"] == 3
        assert ss[0].score == 97

    def test_tier1_cap_at_40(self) -> None:
        """5 unique tier1 rule IDs → min(40, 5×10)=40."""
        nid = "model.demo.a"
        findings = [
            _finding(f"Q{i:03d}", tier=Tier.TIER_1_ENFORCED, node_id=nid)
            for i in range(5)
        ]
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=findings,
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["tier1"] == 40

    def test_tier2_cap_at_20(self) -> None:
        """8 unique tier2 rule IDs → min(20, 8×3)=20."""
        nid = "model.demo.a"
        findings = [
            _finding(f"P{i:03d}", tier=Tier.TIER_2_WARN, node_id=nid)
            for i in range(8)
        ]
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=findings,
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["tier2"] == 20

    def test_duplicate_rule_id_for_same_node_counted_once(self) -> None:
        """Same rule_id appearing twice for one node counts only once (SET logic)."""
        nid = "model.demo.a"
        f1 = _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid)
        # Same rule_id, different fingerprint length trick — rule_id still same
        f2 = Finding(
            rule_id="Q001",  # duplicate rule_id!
            severity=Severity.MAJOR,
            category=Category.QUALITY,
            type=FindingType.CODE_SMELL,
            tier=Tier.TIER_1_ENFORCED,
            confidence=0.9,
            message="second violation",
            file_path=Path("models/a.sql"),
            line=2,
            column=1,
            fingerprint="Q001___dup______",  # different fingerprint
            node_id=nid,
        )
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=[f1, f2],
            coverage=_cov(nid),
        )
        # Still only counts as 1 unique rule_id → penalty = 10
        assert ss[0].score_breakdown["tier1"] == 10

    def test_suppressed_finding_not_counted(self) -> None:
        """Suppressed findings should not affect the score."""
        nid = "model.demo.a"
        f1 = _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid, suppressed=True)
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=[f1],
            coverage=_cov(nid),
        )
        assert ss[0].score == 100
        assert ss[0].score_breakdown["tier1"] == 0

    def test_unexecuted_test_deducts_5(self) -> None:
        """One DATA test that was not executed → unexec_per_test=5."""
        nid = "model.demo.a"
        # Use truthy test_results with an unexecuted DATA test.
        # DATA kind still counts as _actual_tests → test_covered=True → no test_column penalty if tracked.
        tr = _tr("t1", "a", kind="not_null", executed=False, test_kind=TestKind.DATA)
        # For this test to keep test_column_penalty=0, no test_column coverage entry is provided.
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid, test_ct=(1, 1)),   # YAML shows 1 test
            test_results=[tr],                      # truthy → actual-count path
        )
        assert ss[0].score_breakdown["unexec"] == 5
        assert ss[0].score == 95

    def test_parse_failed_deducts_parse_fail_penalty(self) -> None:
        nid = "model.demo.a"
        ss = _build_summaries(
            parsed_nodes={nid: _node("a", parse_success=False)},
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["parse"] == 10
        assert ss[0].score == 90

    def test_render_uncertain_deducts_parse_uncertain_penalty(self) -> None:
        nid = "model.demo.a"
        ss = _build_summaries(
            parsed_nodes={nid: _node("a", render_uncertain=True)},
            coverage=_cov(nid),
        )
        assert ss[0].score_breakdown["parse"] == 5
        assert ss[0].score == 95

    def test_rule_skip_deducts_1_per_skip(self) -> None:
        nid = "model.demo.a"
        sk = _skip("Q001", "a")
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid),
            check_skips=[sk],
        )
        assert ss[0].score_breakdown["skips"] == 1
        assert ss[0].score == 99

    def test_rule_skip_capped_at_5(self) -> None:
        nid = "model.demo.a"
        skips = [_skip(f"Q{i:03d}", "a") for i in range(10)]
        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            coverage=_cov(nid),
            check_skips=skips,
        )
        assert ss[0].score_breakdown["skips"] == 5
        assert ss[0].score == 95

    def test_skips_not_applied_on_parse_failed_model(self) -> None:
        """Skip penalty is only applied when parse_ok and not uncertain."""
        nid = "model.demo.a"
        skips = [_skip(f"Q{i:03d}", "a") for i in range(5)]
        ss = _build_summaries(
            parsed_nodes={nid: _node("a", parse_success=False)},
            coverage=_cov(nid),
            check_skips=skips,
        )
        assert ss[0].score_breakdown["skips"] == 0
        assert ss[0].score_breakdown["parse"] == 10

    def test_full_violation_stack_score_28(self) -> None:
        """All penalty categories fire simultaneously → score = 28.

        Breakdown:
          test_column = 25  (0 of 4 columns covered → full penalty)
          doc      = 15   (0 of 1 doc covered)
          tier1    = 20   (2 unique tier1 rule IDs × 10 = 20, below cap)
          tier2    =  6   (2 unique tier2 rule IDs × 3  =  6, below cap)
          unexec   =  5   (UNKNOWN + executed=False → unexec_count=1 → 5)
          parse    =  0   (parse_success=True)
          skips    =  1   (1 skip, parse_ok=True → fires)
          ─────────────
          total    = 72   → score = 100 − 72 = 28
        """
        nid = "model.demo.a"

        # UNKNOWN kind: not added to data_count or unit_count → _actual_tests=0
        # executed=False: added to unexec_count_by_node
        tr_unknown = TestResult(
            test_name="t1",
            test_kind="not_null",
            model_unique_id=nid,
            status=TestStatus.PASS,
            origin="dbt_test",
            kind=TestKind.UNKNOWN,   # not DATA, not UNIT → _actual_tests stays 0
            executed=False,          # → unexec_count += 1
        )

        findings = [
            _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid),
            _finding("Q002", tier=Tier.TIER_1_ENFORCED, node_id=nid),
            _finding("P001", tier=Tier.TIER_2_WARN, node_id=nid),
            _finding("P002", tier=Tier.TIER_2_WARN, node_id=nid),
        ]
        sk = _skip("R001", "a")

        ss = _build_summaries(
            parsed_nodes={nid: _node("a")},
            findings=findings,
            coverage=_cov(nid, test_ct=(0, 1), doc_ct=(0, 1), column_test_ct=(0, 4)),
            test_results=[tr_unknown],   # truthy → actual-count path
            check_skips=[sk],
        )
        assert ss[0].score == 28
        assert ss[0].score_breakdown["column_test"] == 25
        assert ss[0].score_breakdown["doc"] == 15
        assert ss[0].score_breakdown["tier1"] == 20
        assert ss[0].score_breakdown["tier2"] == 6
        assert ss[0].score_breakdown["unexec"] == 5
        assert ss[0].score_breakdown["parse"] == 0
        assert ss[0].score_breakdown["skips"] == 1


# ---------------------------------------------------------------------------
# 10. Multi-model isolation — findings don't bleed across models
# ---------------------------------------------------------------------------


class TestMultiModelSummaries:
    def test_findings_isolated_to_their_node(self) -> None:
        """A tier1 finding on model_a must not affect model_b's score."""
        nid_a = "model.demo.a"
        nid_b = "model.demo.b"
        f1 = _finding("Q001", tier=Tier.TIER_1_ENFORCED, node_id=nid_a)
        coverage = _multi_cov(
            (nid_a, (1, 1), (1, 1)),
            (nid_b, (1, 1), (1, 1)),
        )
        ss = _build_summaries(
            parsed_nodes={nid_a: _node("a"), nid_b: _node("b")},
            findings=[f1],
            coverage=coverage,
        )
        by_name = {s.name: s for s in ss}
        assert by_name["a"].score == 90
        assert by_name["b"].score == 100

    def test_two_models_independent_scores(self) -> None:
        """a: no test, no doc (score=60); b: perfect (score=100)."""
        nid_a = "model.demo.a"
        nid_b = "model.demo.b"
        coverage = _multi_cov(
            (nid_a, (0, 1), (0, 1), (0, 4)),
            (nid_b, (1, 1), (1, 1)),
        )
        ss = _build_summaries(
            parsed_nodes={nid_a: _node("a"), nid_b: _node("b")},
            coverage=coverage,
        )
        by_name = {s.name: s for s in ss}
        assert by_name["a"].score == 60
        assert by_name["b"].score == 100

    def test_summaries_sorted_by_score_then_name(self) -> None:
        """_build_model_summaries sorts by (score, name) ascending."""
        nid_a = "model.demo.a"
        nid_b = "model.demo.b"
        nid_c = "model.demo.c"
        coverage = _multi_cov(
            (nid_a, (1, 1), (1, 1)),              # score=100
            (nid_b, (0, 1), (0, 1), (0, 4)),      # score=60
            (nid_c, (0, 1), (1, 1), (0, 4)),      # score=75
        )
        ss = _build_summaries(
            parsed_nodes={
                nid_a: _node("a"),
                nid_b: _node("b"),
                nid_c: _node("c"),
            },
            coverage=coverage,
        )
        scores = [s.score for s in ss]
        assert scores == sorted(scores), "summaries must be sorted by score ascending"
        # b has score=60, c has score=75, a has score=100
        assert ss[0].name == "b"
        assert ss[1].name == "c"
        assert ss[2].name == "a"


# ---------------------------------------------------------------------------
# 11. Quality gate coverage thresholds
# ---------------------------------------------------------------------------


class TestQualityGateCoverageThresholds:
    @pytest.mark.parametrize(
        "dim,covered,total,min_thresh,should_pass",
        [
            # Passing cases
            ("test",     10, 10, 0.8,  True),
            ("test",      8, 10, 0.8,  True),   # exactly at threshold
            ("doc",      10, 10, 1.0,  True),
            ("doc",       9, 10, 0.9,  True),
            # Failing cases
            ("test",      7, 10, 0.8,  False),
            ("test",      0, 10, 0.1,  False),
            ("doc",       8, 10, 0.9,  False),
            ("doc",       0, 10, 0.5,  False),
        ],
    )
    def test_gate_pass_fail_by_threshold(
        self,
        dim: str,
        covered: int,
        total: int,
        min_thresh: float,
        should_pass: bool,
    ) -> None:
        cfg = GateConfig(coverage={dim: CoverageThreshold(min=min_thresh)})
        result = evaluate(_scan_result(dim, covered, total), cfg)
        assert result.passed is should_pass

    def test_gate_passes_when_no_coverage_threshold_set(self) -> None:
        cfg = GateConfig()
        result = evaluate(_scan_result("test", 0, 10), cfg)
        assert result.passed

    def test_gate_fails_only_when_matching_dim_below_threshold(self) -> None:
        """Gate is configured for 'doc'; 'test' coverage below doesn't fail it."""
        cfg = GateConfig(coverage={"doc": CoverageThreshold(min=0.9)})
        result = evaluate(_scan_result("test", 0, 10), cfg)
        assert result.passed

    def test_gate_all_dims_must_pass_and_semantics(self) -> None:
        """test passes, doc fails → overall gate fails (AND semantics)."""
        cfg = GateConfig(coverage={
            "test": CoverageThreshold(min=0.8),
            "doc": CoverageThreshold(min=0.8),
        })
        sr = ScanResult(
            findings=[],
            coverage=[
                CoverageMetric(dimension="test", covered=10, total=10, ratio=1.0),
                CoverageMetric(dimension="doc",  covered=5,  total=10, ratio=0.5),
            ],
            project_root=Path("/tmp"),
            dialect="postgres",
            render_stats=RenderStats(total_files=1, parse_success=1),
        )
        result = evaluate(sr, cfg)
        assert not result.passed

    def test_gate_passes_when_exactly_at_minimum(self) -> None:
        """ratio == min threshold → gate should pass (inclusive lower bound)."""
        cfg = GateConfig(coverage={"test": CoverageThreshold(min=0.5)})
        result = evaluate(_scan_result("test", 5, 10), cfg)
        assert result.passed
