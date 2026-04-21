"""SPEC-31 — unit tests for WaiverResolver."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from dbt_coverage.analyzers.waivers import (
    BaselineEntry,
    BaselineFile,
    WaiverResolver,
    capture_baseline,
    load_baseline_for,
)
from dbt_coverage.core import (
    Category,
    Finding,
    FindingType,
    Severity,
    SuppressionSource,
    Tier,
    compute_fingerprint,
)
from dbt_coverage.utils.config import DbtcovConfig, OverrideEntry


def _finding(
    rule_id: str = "R001",
    node_id: str = "model.demo.mart_orders",
    file_path: str = "models/marts/mart_orders.sql",
    line: int = 10,
) -> Finding:
    fp = compute_fingerprint(rule_id, file_path, f"{node_id}:{line}")
    return Finding(
        rule_id=rule_id,
        severity=Severity.MAJOR,
        category=Category.REFACTOR,
        type=FindingType.CODE_SMELL,
        tier=Tier.TIER_2_WARN,
        confidence=0.9,
        message="duplicate CTE",
        file_path=Path(file_path),
        line=line,
        column=1,
        node_id=node_id,
        fingerprint=fp,
    )


def _cfg(overrides: list[OverrideEntry]) -> DbtcovConfig:
    return DbtcovConfig(overrides=overrides)


# ---------------------------------------------------------------- overrides


def test_override_suppresses_matching_finding_by_model():
    f = _finding()
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            models=["mart_orders"],
            reason="intentional duplication for perf",
            reviewer="alice",
        )
    ])
    res = WaiverResolver(cfg).apply([f])
    stamped, extra = res
    assert extra == []
    assert stamped[0].suppressed is True
    assert stamped[0].suppression is not None
    assert stamped[0].suppression.source == SuppressionSource.OVERRIDE
    assert stamped[0].suppression.reviewer == "alice"


def test_override_by_path_glob():
    f = _finding(file_path="models/marts/mart_orders.sql")
    cfg = _cfg([
        OverrideEntry(
            waive=["*"],
            paths=["models/marts/*.sql"],
            reason="legacy mart",
        )
    ])
    stamped, _ = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is True


def test_override_by_node_id_exact():
    f = _finding()
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            node_ids=["model.demo.mart_orders"],
            reason="pinned",
        )
    ])
    stamped, _ = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is True


def test_override_rule_glob():
    f = _finding(rule_id="R005")
    cfg = _cfg([
        OverrideEntry(
            waive=["R*"],
            models=["mart_orders"],
            reason="all refactor rules",
        )
    ])
    stamped, _ = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is True


def test_override_does_not_match_wrong_rule():
    f = _finding(rule_id="R001")
    cfg = _cfg([
        OverrideEntry(
            waive=["R002"],
            models=["mart_orders"],
            reason="nope",
        )
    ])
    stamped, _ = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is False


def test_override_requires_selector_match():
    f = _finding()
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            models=["different_model"],
            reason="wrong target",
        )
    ])
    stamped, _ = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is False


# ---------------------------------------------------------------- expiry


def test_expired_waiver_reactivates_finding_and_emits_g003():
    f = _finding()
    yesterday = date.today() - timedelta(days=1)
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            models=["mart_orders"],
            reason="time-boxed",
            reviewer="alice",
            expires=yesterday,
        )
    ])
    stamped, extra = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is False, "expired waiver must re-activate finding"
    assert len(extra) == 1
    assert extra[0].rule_id == "G003"
    assert extra[0].severity == Severity.MAJOR
    assert "expired" in extra[0].message.lower()


def test_future_expiry_still_waives():
    f = _finding()
    tomorrow = date.today() + timedelta(days=7)
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            models=["mart_orders"],
            reason="still in grace",
            expires=tomorrow,
        )
    ])
    stamped, extra = WaiverResolver(cfg).apply([f])
    assert stamped[0].suppressed is True
    assert extra == []


# ---------------------------------------------------------------- baseline


def test_baseline_suppresses_matching_fingerprint():
    f = _finding()
    baseline = BaselineFile(
        schema_version=1,
        captured_at=None,
        entries=[
            BaselineEntry(
                fingerprint=f.fingerprint,
                rule_id=f.rule_id,
                node_id=f.node_id,
                path=str(f.file_path),
                reason="initial baseline",
            )
        ],
    )
    stamped, extra = WaiverResolver(DbtcovConfig(), baseline=baseline).apply([f])
    assert stamped[0].suppressed is True
    assert stamped[0].suppression.source == SuppressionSource.BASELINE
    assert extra == []


def test_override_wins_over_baseline():
    f = _finding()
    baseline = BaselineFile(
        schema_version=1,
        captured_at=None,
        entries=[
            BaselineEntry(fingerprint=f.fingerprint, rule_id="R001", node_id=None, path=None)
        ],
    )
    cfg = _cfg([
        OverrideEntry(
            waive=["R001"],
            models=["mart_orders"],
            reason="override",
        )
    ])
    stamped, _ = WaiverResolver(cfg, baseline=baseline).apply([f])
    assert stamped[0].suppression.source == SuppressionSource.OVERRIDE


# ---------------------------------------------------------------- I/O


def test_load_baseline_for_reads_dbtcov_dir(tmp_path: Path):
    (tmp_path / ".dbtcov").mkdir()
    b = tmp_path / ".dbtcov" / "baseline.json"
    b.write_text(
        json.dumps({"schema_version": 1, "entries": [{"fingerprint": "abc"}]}),
        encoding="utf-8",
    )
    loaded = load_baseline_for(tmp_path, explicit=None)
    assert loaded is not None
    assert loaded.entries[0].fingerprint == "abc"


def test_load_baseline_for_missing_returns_none(tmp_path: Path):
    assert load_baseline_for(tmp_path, explicit=None) is None


def test_capture_baseline_excludes_already_suppressed():
    f1 = _finding(rule_id="R001")
    f2 = _finding(rule_id="R002", line=20)
    f2 = f2.model_copy(update={"suppressed": True})
    payload = capture_baseline([f1, f2], captured_at="2030-01-01T00:00:00Z")
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["rule_id"] == "R001"
    assert payload["schema_version"] == 1
    assert payload["captured_at"] == "2030-01-01T00:00:00Z"
