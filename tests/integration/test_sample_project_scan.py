"""SPEC-13 §5 — end-to-end scan on the packaged sample dbt project."""

from __future__ import annotations

from dbt_coverage.core import ScanResult, Tier


def test_scan_runs_without_crash(scan_result: ScanResult) -> None:
    assert scan_result is not None
    assert scan_result.dialect == "postgres"
    assert scan_result.project_name == "sample_project"
    assert scan_result.render_stats.total_files >= 4


def test_models_parse_successfully(scan_result: ScanResult) -> None:
    rs = scan_result.render_stats
    assert rs.parse_success == rs.total_files, (
        f"expected all models to parse: {rs.parse_failed} failed"
    )


def test_q001_detects_select_star(scan_result: ScanResult) -> None:
    q001 = [f for f in scan_result.findings if f.rule_id == "Q001"]
    assert q001, "expected Q001 finding on stg_orders"
    assert any("stg_orders" in str(f.file_path) for f in q001)


def test_p001_detects_cross_join(scan_result: ScanResult) -> None:
    p001 = [f for f in scan_result.findings if f.rule_id == "P001"]
    assert p001, "expected P001 finding on dim_customers cross join"
    assert any("dim_customers" in str(f.file_path) for f in p001)


def test_coverage_computed(scan_result: ScanResult) -> None:
    dims = {m.dimension: m for m in scan_result.coverage}
    assert "test" in dims
    assert "doc" in dims
    assert dims["test"].total >= 4
    assert dims["doc"].total >= 4


def test_all_findings_have_valid_fingerprint(scan_result: ScanResult) -> None:
    for f in scan_result.findings:
        assert f.fingerprint
        assert len(f.fingerprint) >= 16


def test_all_findings_have_relative_paths(scan_result: ScanResult) -> None:
    for f in scan_result.findings:
        assert not f.file_path.is_absolute(), f


def test_tier_distribution_non_empty(scan_result: ScanResult) -> None:
    tiers = {f.tier for f in scan_result.findings}
    assert tiers, "expected at least one finding emitted"
    assert tiers.issubset({Tier.TIER_1_ENFORCED, Tier.TIER_2_WARN})


def test_json_roundtrip(scan_result: ScanResult) -> None:
    payload = scan_result.model_dump_json()
    reloaded = ScanResult.model_validate_json(payload)
    assert reloaded.render_stats.total_files == scan_result.render_stats.total_files
    assert len(reloaded.findings) == len(scan_result.findings)
