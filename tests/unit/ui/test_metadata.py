from __future__ import annotations

from dbt_coverage_ui.metadata import (
    CONFIG_FIELDS,
    DIMENSION_DESCRIPTIONS,
    RULE_DESCRIPTIONS,
    rule_meta,
)


def test_every_dimension_has_metadata() -> None:
    for dim in ("test", "test_meaningful", "test_weighted_cc", "test_unit", "doc", "complexity"):
        assert dim in DIMENSION_DESCRIPTIONS
        assert DIMENSION_DESCRIPTIONS[dim]["desc"]


def test_known_rules_have_descriptions() -> None:
    for rid in ("Q001", "Q005", "P001", "T001", "T002", "R001", "A001", "S001", "G001"):
        assert rid in RULE_DESCRIPTIONS
        assert RULE_DESCRIPTIONS[rid]["name"]
        assert RULE_DESCRIPTIONS[rid]["category"]


def test_rule_meta_fallback() -> None:
    fallback = rule_meta("ZZZ_UNKNOWN")
    assert fallback["name"] == "ZZZ_UNKNOWN"
    assert fallback["category"] == "UNKNOWN"


def test_config_fields_present() -> None:
    for key in ("render.mode", "dialect", "complexity.threshold_warn"):
        assert key in CONFIG_FIELDS
        assert CONFIG_FIELDS[key]["desc"]
