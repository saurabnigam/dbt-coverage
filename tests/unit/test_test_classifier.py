"""SPEC-22 §5 — tests for the test classifier."""

from __future__ import annotations

from dbt_coverage.coverage import TestClass, classify
from dbt_coverage.utils import TestOverrides, WeightTable


def test_default_trivial_kinds() -> None:
    w = WeightTable()
    o = TestOverrides()
    for k in ("not_null", "unique"):
        cls, weight = classify(k, o, w)
        assert cls is TestClass.TRIVIAL
        assert weight == 0.0


def test_default_structural_kinds() -> None:
    w = WeightTable()
    o = TestOverrides()
    for k in ("relationships", "accepted_values", "dbt_utils.not_constant"):
        cls, weight = classify(k, o, w)
        assert cls is TestClass.STRUCTURAL
        assert weight == 0.25


def test_unknown_kind_is_logical() -> None:
    w = WeightTable()
    o = TestOverrides()
    cls, weight = classify("my_org.custom_generic", o, w)
    assert cls is TestClass.LOGICAL
    assert weight == 1.0


def test_empty_kind_is_unknown() -> None:
    w = WeightTable()
    o = TestOverrides()
    cls, weight = classify("", o, w)
    assert cls is TestClass.UNKNOWN
    assert weight == 0.0


def test_override_wins_over_default() -> None:
    w = WeightTable()
    o = TestOverrides(logical=["unique"])
    cls, weight = classify("unique", o, w)
    assert cls is TestClass.LOGICAL
    assert weight == 1.0


def test_glob_override() -> None:
    w = WeightTable()
    o = TestOverrides(structural=["my_org.*"])
    cls, weight = classify("my_org.fk_soft", o, w)
    assert cls is TestClass.STRUCTURAL
    assert weight == 0.25


def test_classifier_respects_custom_weights() -> None:
    w = WeightTable(trivial=0.1, structural=0.4, logical=0.9, unknown=0.05)
    o = TestOverrides()
    assert classify("not_null", o, w) == (TestClass.TRIVIAL, 0.1)
    assert classify("relationships", o, w) == (TestClass.STRUCTURAL, 0.4)
    assert classify("some_generic", o, w) == (TestClass.LOGICAL, 0.9)
    assert classify("", o, w) == (TestClass.UNKNOWN, 0.05)
