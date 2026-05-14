"""File-backed quality score tests using the quality_score_scenarios fixture project.

These tests load ``tests/fixtures/quality_score_scenarios/`` through
``orchestrator.scan()`` (module-scoped, runs once per session) and assert on the
real ``ModelSummary`` objects and ``CoverageMetric`` values produced by the full
pipeline: YAML parsing → SQL rendering → SQL parsing → rule engine → coverage
computation → scoring.

This is complementary to ``test_quality_score.py`` (which unit-tests individual
coverage functions in memory with synthetic data).

Fixture project layout
----------------------
model_perfect            score=100  full docs + relationship test (logical), no violations
model_no_test            score=75   full docs, zero tests → no_test_penalty=25
model_no_doc             score=85   tests exist, no model description → doc_penalty=15
model_no_test_no_doc     score=60   no docs, no tests → -25 -15
model_trivial_tests_only score=100  not_null/unique only (TRIVIAL) but YAML tests exist
model_partial_doc        score=100  model description present; column gaps don't reduce score
model_with_tier1_violations score=97  SELECT * triggers Q001 (tier2 by default) → -3
model_high_complexity    score=94   SELECT * (Q001) + CC=17 ≥ 15 (Q003) → tier2=-6

Coverage dimensions (project-wide, noise rules disabled in dbtcov.yml)
-----------------------------------------------------------------------
test              6/8  (model_no_test + model_no_test_no_doc are uncovered)
test_weighted_cc  0/24 (all models have trivial-only tests → best_weight=0)
complexity        7/8  (only model_high_complexity has CC > threshold_warn=15)

How high cyclomatic complexity interacts with test coverage
-----------------------------------------------------------
The ``test`` dimension just asks: "does the model have ≥1 test declared?"
High CC does NOT change that answer.  The ``test_weighted_cc`` dimension uses:

    ratio = Σ(best_weight(m) × cc(m)) / Σ(cc(m))

where ``best_weight`` is the highest classification weight among the model's
passing tests (trivial=0.0, structural=0.25, logical=1.0).  A model with
CC=17 and only trivial tests contributes 0×17=0 to the numerator but 17 to
the denominator — a 17× bigger drag than a CC=1 model with the same tests.

See ``TestHighComplexityTestCoverageImpact`` for per-node assertions and a
synthetic in-memory demonstration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dbt_coverage.core import ComplexityMetrics, CoverageMetric, ModelSummary, ParsedNode, RenderMode, TestKind, TestResult, TestStatus
from dbt_coverage.coverage import compute_test_cc_weighted_coverage
from dbt_coverage.utils import DbtcovConfig

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "quality_score_scenarios"
_PROJECT_PREFIX = "model.quality_score_scenarios."


def _summaries(bundle) -> dict[str, ModelSummary]:
    """Return {model_name: ModelSummary} from a ScanBundle."""
    return {s.name: s for s in bundle.result.model_summaries}


def _cov_map(bundle) -> dict[str, CoverageMetric]:
    """Return {dimension: CoverageMetric} from a ScanBundle."""
    return {m.dimension: m for m in bundle.result.coverage}


def _node_id(model_name: str) -> str:
    return f"{_PROJECT_PREFIX}{model_name}"


# ---------------------------------------------------------------------------
# Module-scoped fixtures that derive from the session-scoped quality_score_bundle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def summaries(quality_score_bundle) -> dict[str, ModelSummary]:
    return _summaries(quality_score_bundle)


@pytest.fixture(scope="module")
def cov(quality_score_bundle) -> dict[str, CoverageMetric]:
    return _cov_map(quality_score_bundle)


@pytest.fixture(scope="module")
def complexity_map(quality_score_bundle) -> dict[str, ComplexityMetrics]:
    return quality_score_bundle.result.complexity


# ---------------------------------------------------------------------------
# Per-model test classes
# ---------------------------------------------------------------------------


class TestModelPerfect:
    """model_perfect: full docs, relationship test (logical), no violations.
    Expected score = 100 with all breakdown penalties at zero.
    """

    def test_score_is_100(self, summaries):
        assert summaries["model_perfect"].score == 100

    def test_test_covered(self, summaries):
        assert summaries["model_perfect"].test_covered is True

    def test_doc_ratio_is_full(self, summaries):
        assert summaries["model_perfect"].doc_ratio == 1.0

    def test_no_tier1_violations(self, summaries):
        assert summaries["model_perfect"].tier1_rules == []

    def test_no_tier2_violations(self, summaries):
        assert summaries["model_perfect"].tier2_rules == []

    def test_all_breakdown_penalties_zero(self, summaries):
        bd = summaries["model_perfect"].score_breakdown
        assert bd["column_test"] == 0
        assert bd["doc"] == 0
        assert bd["tier1"] == 0
        assert bd["tier2"] == 0
        assert bd["unexec"] == 0
        assert bd["parse"] == 0


class TestModelNoTest:
    """model_no_test: full docs, zero tests declared.
    Expected score = 75 (no_test_penalty = 25).
    """

    def test_score_is_75(self, summaries):
        assert summaries["model_no_test"].score == 75

    def test_not_test_covered(self, summaries):
        assert summaries["model_no_test"].test_covered is False

    def test_doc_ratio_is_full(self, summaries):
        assert summaries["model_no_test"].doc_ratio == 1.0

    def test_no_test_penalty_in_breakdown(self, summaries):
        assert summaries["model_no_test"].score_breakdown["column_test"] == 25

    def test_doc_penalty_zero(self, summaries):
        assert summaries["model_no_test"].score_breakdown["doc"] == 0

    def test_no_violations(self, summaries):
        s = summaries["model_no_test"]
        assert s.tier1_rules == []
        assert s.tier2_rules == []


class TestModelNoDoc:
    """model_no_doc: tests exist, no model-level description.
    Expected score = 85 (doc_penalty = 15 = doc_penalty_max).
    """

    def test_score_is_85(self, summaries):
        assert summaries["model_no_doc"].score == 85

    def test_test_covered(self, summaries):
        assert summaries["model_no_doc"].test_covered is True

    def test_doc_ratio_is_zero(self, summaries):
        assert summaries["model_no_doc"].doc_ratio == 0.0

    def test_doc_penalty_in_breakdown(self, summaries):
        assert summaries["model_no_doc"].score_breakdown["doc"] == 15

    def test_no_test_penalty_zero(self, summaries):
        assert summaries["model_no_doc"].score_breakdown["column_test"] == 0

    def test_no_violations(self, summaries):
        s = summaries["model_no_doc"]
        assert s.tier1_rules == []
        assert s.tier2_rules == []


class TestModelNoTestNoDoc:
    """model_no_test_no_doc: no description and no tests.
    Expected score = 60 (no_test=25 + doc=15).
    """

    def test_score_is_60(self, summaries):
        assert summaries["model_no_test_no_doc"].score == 60

    def test_not_test_covered(self, summaries):
        assert summaries["model_no_test_no_doc"].test_covered is False

    def test_doc_ratio_is_zero(self, summaries):
        assert summaries["model_no_test_no_doc"].doc_ratio == 0.0

    def test_no_test_penalty_in_breakdown(self, summaries):
        assert summaries["model_no_test_no_doc"].score_breakdown["column_test"] == 25

    def test_doc_penalty_in_breakdown(self, summaries):
        assert summaries["model_no_test_no_doc"].score_breakdown["doc"] == 15

    def test_combined_penalty_accounts_for_score(self, summaries):
        bd = summaries["model_no_test_no_doc"].score_breakdown
        assert bd["column_test"] + bd["doc"] == 40


class TestModelTrivialTestsOnly:
    """model_trivial_tests_only: only not_null/unique tests (TRIVIAL class, weight=0).
    Expected score = 100 because YAML tests exist → no_test_penalty = 0.
    The test_meaningful coverage dimension shows 0%, but that does NOT deduct from score.
    """

    def test_score_is_100(self, summaries):
        assert summaries["model_trivial_tests_only"].score == 100

    def test_test_covered(self, summaries):
        # YAML declares tests → test_covered = True → no_test_penalty = 0
        assert summaries["model_trivial_tests_only"].test_covered is True

    def test_no_test_penalty_zero(self, summaries):
        assert summaries["model_trivial_tests_only"].score_breakdown["column_test"] == 0

    def test_no_violations(self, summaries):
        s = summaries["model_trivial_tests_only"]
        assert s.tier1_rules == []
        assert s.tier2_rules == []


class TestModelPartialDoc:
    """model_partial_doc: model description present, only 1 of 4 columns documented.

    doc_ratio = 2/5 = 0.4:
      - model description counts as 1 covered, 1 total
      - 4 columns: only payment_id has a description → 1 covered, 4 total
      → total: 2 covered / 5 total = 0.4

    doc_penalty = round((1 - 0.4) × 15) = round(9) = 9
    Expected score = 100 − 9 = 91.

    Key insight: column-level documentation DOES affect the doc_ratio and hence
    the quality score. This scenario demonstrates that gap.
    """

    def test_score_is_91(self, summaries):
        # 2 of 5 items documented (model desc + 1 of 4 columns)
        assert summaries["model_partial_doc"].score == 91

    def test_doc_ratio_is_0_4(self, summaries):
        # 2 covered (model desc + payment_id desc) out of 5 total (model + 4 cols)
        assert summaries["model_partial_doc"].doc_ratio == pytest.approx(0.4, abs=0.01)

    def test_test_covered(self, summaries):
        assert summaries["model_partial_doc"].test_covered is True

    def test_doc_penalty_reflects_column_gaps(self, summaries):
        # round((1 - 0.4) × 15) = round(9) = 9
        assert summaries["model_partial_doc"].score_breakdown["doc"] == 9

    def test_no_test_or_violation_penalties(self, summaries):
        bd = summaries["model_partial_doc"].score_breakdown
        assert bd["column_test"] == 0
        assert bd["tier1"] == 0
        assert bd["tier2"] == 0


class TestModelWithTier1Violations:
    """model_with_tier1_violations: SELECT * triggers Q001 (tier-2 by default).
    Expected score = 97 (1 tier2 rule × 3 = 3 penalty).
    """

    def test_score_is_97(self, summaries):
        assert summaries["model_with_tier1_violations"].score == 97

    def test_q001_in_tier2_rules(self, summaries):
        assert "Q001" in summaries["model_with_tier1_violations"].tier2_rules

    def test_no_tier1_rules(self, summaries):
        assert summaries["model_with_tier1_violations"].tier1_rules == []

    def test_tier2_penalty_is_3(self, summaries):
        assert summaries["model_with_tier1_violations"].score_breakdown["tier2"] == 3

    def test_no_test_or_doc_penalty(self, summaries):
        bd = summaries["model_with_tier1_violations"].score_breakdown
        assert bd["column_test"] == 0
        assert bd["doc"] == 0

    def test_test_covered(self, summaries):
        assert summaries["model_with_tier1_violations"].test_covered is True

    def test_doc_ratio_is_full(self, summaries):
        assert summaries["model_with_tier1_violations"].doc_ratio == 1.0


class TestModelHighComplexity:
    """model_high_complexity: CC=17 fires Q003; final SELECT * in enriched CTE fires Q001.
    Both are tier-2 rules → tier2 penalty = 2 × 3 = 6.
    Expected score = 94.
    """

    def test_score_is_94(self, summaries):
        assert summaries["model_high_complexity"].score == 94

    def test_q001_in_tier2_rules(self, summaries):
        assert "Q001" in summaries["model_high_complexity"].tier2_rules

    def test_q003_in_tier2_rules(self, summaries):
        assert "Q003" in summaries["model_high_complexity"].tier2_rules

    def test_tier2_penalty_is_6(self, summaries):
        assert summaries["model_high_complexity"].score_breakdown["tier2"] == 6

    def test_no_tier1_rules(self, summaries):
        assert summaries["model_high_complexity"].tier1_rules == []

    def test_no_test_or_doc_penalty(self, summaries):
        bd = summaries["model_high_complexity"].score_breakdown
        assert bd["column_test"] == 0
        assert bd["doc"] == 0

    def test_cc_is_above_threshold(self, complexity_map):
        nid = _node_id("model_high_complexity")
        assert nid in complexity_map
        assert complexity_map[nid].cc > 15  # threshold_warn default


# ---------------------------------------------------------------------------
# Project-wide aggregate coverage assertions
# ---------------------------------------------------------------------------


class TestAggregateCoverageMetrics:
    """Assert on the project-level CoverageMetric values produced by the scan.

    Reference (from dbtcov scan output with noise rules disabled):
      test              6/8   (uncovered: model_no_test, model_no_test_no_doc)
      test_weighted_cc  0/24  (all models have trivial-only tests → best_weight=0)
      complexity        7/8   (uncovered: model_high_complexity, CC=17 > threshold_warn=15)
    """

    def test_test_coverage_total_is_8(self, cov):
        assert cov["test"].total == 8

    def test_test_coverage_covered_is_6(self, cov):
        assert cov["test"].covered == 6

    def test_test_coverage_uncovered_models_are_no_test_variants(self, cov):
        # The two uncovered models must be the ones with no tests declared
        uncovered = {nid for nid, (c, _t) in cov["test"].per_node.items() if c == 0}
        assert _node_id("model_no_test") in uncovered
        assert _node_id("model_no_test_no_doc") in uncovered

    def test_weighted_cc_ratio_is_zero(self, cov):
        # All models have only trivial tests (not_null/unique, weight=0.0)
        # → numerator = 0 for every model → overall ratio = 0.0
        assert cov["test_weighted_cc"].ratio == 0.0

    def test_weighted_cc_total_equals_sum_of_cc(self, cov):
        # Total denominator is the sum of CC across all models.
        # The scan shows total=24 (CC=17 for high_complexity + 1 each for remaining 7).
        assert cov["test_weighted_cc"].total == 24

    def test_complexity_total_is_8(self, cov):
        assert cov["complexity"].total == 8

    def test_complexity_covered_is_7(self, cov):
        # Only model_high_complexity fails the CC threshold
        assert cov["complexity"].covered == 7

    def test_complexity_uncovered_is_high_complexity_model(self, cov):
        uncovered = {nid for nid, (c, _t) in cov["complexity"].per_node.items() if c == 0}
        assert _node_id("model_high_complexity") in uncovered


# ---------------------------------------------------------------------------
# TestHighComplexityTestCoverageImpact
# ---------------------------------------------------------------------------


class TestHighComplexityTestCoverageImpact:
    """Demonstrates and tests exactly how high cyclomatic complexity interacts with
    each coverage dimension.

    Key insight:
      • ``test`` dimension   → binary: model has tests in YAML → covered
      • ``test_meaningful``  → only LOGICAL tests count (not_null/unique are TRIVIAL)
      • ``test_weighted_cc`` → ratio = Σ(best_weight × cc) / Σ(cc)
                               High CC amplifies the drag of trivial tests by the CC multiplier.

    The high-complexity model (CC=17) has only trivial tests:
      contribution to numerator:   best_weight(0.0) × CC(17) = 0
      contribution to denominator: CC(17) = 17

    vs a CC=1 model with identical trivial tests:
      contribution to numerator:   0 × 1 = 0
      contribution to denominator: 1

    So model_high_complexity accounts for 17/24 = 71% of the denominator while
    contributing zero to the numerator — it is the dominant drag on test_weighted_cc.
    """

    def test_test_covered_true_because_yaml_tests_declared(self, summaries):
        # Plain test coverage only checks: "does the model have ≥1 test in YAML?"
        # not_null/unique on model_high_complexity → test_covered = True
        assert summaries["model_high_complexity"].test_covered is True

    def test_no_test_penalty_zero_for_high_cc_model(self, summaries):
        # YAML tests exist → test_column_penalty = 0 regardless of CC
        assert summaries["model_high_complexity"].score_breakdown["column_test"] == 0

    def test_weighted_cc_per_node_covered_is_zero(self, cov):
        # best_weight(trivial) = 0.0 → 0.0 × 17 = 0 contribution to numerator
        nid = _node_id("model_high_complexity")
        covered, _total = cov["test_weighted_cc"].per_node[nid]
        assert covered == 0

    def test_weighted_cc_per_node_total_equals_cc(self, cov, complexity_map):
        # Denominator slot = CC of the model
        nid = _node_id("model_high_complexity")
        _covered, total = cov["test_weighted_cc"].per_node[nid]
        assert total == complexity_map[nid].cc

    def test_high_cc_model_dominates_weighted_denominator(self, cov, complexity_map):
        # model_high_complexity's CC alone is > half the total denominator
        nid = _node_id("model_high_complexity")
        model_cc = complexity_map[nid].cc
        assert model_cc / cov["test_weighted_cc"].total > 0.5

    def test_complexity_coverage_not_covered(self, cov):
        # CC=17 > threshold_warn=15 → not complexity-covered
        nid = _node_id("model_high_complexity")
        covered, _total = cov["complexity"].per_node[nid]
        assert covered == 0

    def test_logical_test_would_maximize_weighted_cc_ratio(self):
        """In-memory demonstration: replacing trivial tests with a logical test on a
        CC=17 model makes the weighted ratio jump from 0.0 to 1.0.

        This shows the correct fix: add a logical/custom test (singular SQL test,
        accepted_values with business meaning, or a relationship check) to a
        high-complexity model so it gets full weight.
        """
        # Build a minimal in-memory parsed node: one model, CC=17, logical test
        nid = "model.demo.high_cc"
        parsed_nodes = {
            nid: ParsedNode(
                file_path=Path("models/high_cc.sql"),
                node_id=nid,
                source_sql="select 1 as id",
                rendered_sql="select 1 as id",
                render_mode=RenderMode.MOCK,
                parse_success=True,
                render_uncertain=False,
            )
        }
        complexity = {nid: ComplexityMetrics(cc=17)}
        # A singular/logical test (kind string not in trivial set → weight=1.0)
        test_results = [
            TestResult(
                test_name="test_high_cc_model_has_logical_test",
                test_kind="singular",  # classified as LOGICAL (weight=1.0)
                model_unique_id=nid,
                status=TestStatus.PASS,
                origin="dbt_test",
                kind=TestKind.DATA,
                executed=True,
            )
        ]
        metric = compute_test_cc_weighted_coverage(
            parsed_nodes,
            complexity,
            test_results,
            DbtcovConfig(),
        )
        # With CC=17 and best_weight=1.0: covered=17, total=17 → ratio=1.0
        assert metric.ratio == 1.0
        covered, total = metric.per_node[nid]
        assert covered == 17
        assert total == 17

    def test_trivial_test_gives_zero_weighted_coverage_at_any_cc(self):
        """In-memory: trivial test (not_null) on a CC=17 model → ratio=0.0.
        Contrast with the test above to highlight the gap.
        """
        nid = "model.demo.high_cc_trivial"
        parsed_nodes = {
            nid: ParsedNode(
                file_path=Path("models/high_cc_trivial.sql"),
                node_id=nid,
                source_sql="select 1 as id",
                rendered_sql="select 1 as id",
                render_mode=RenderMode.MOCK,
                parse_success=True,
                render_uncertain=False,
            )
        }
        complexity = {nid: ComplexityMetrics(cc=17)}
        test_results = [
            TestResult(
                test_name="test_id_not_null",
                test_kind="not_null",  # TRIVIAL → weight=0.0
                model_unique_id=nid,
                status=TestStatus.PASS,
                origin="dbt_test",
                kind=TestKind.DATA,
                executed=True,
            )
        ]
        metric = compute_test_cc_weighted_coverage(
            parsed_nodes,
            complexity,
            test_results,
            DbtcovConfig(),
        )
        # best_weight=0.0 → 0×17=0 numerator, 17 denominator → ratio=0.0
        assert metric.ratio == 0.0
        covered, total = metric.per_node[nid]
        assert covered == 0
        assert total == 17  # denominator = CC
