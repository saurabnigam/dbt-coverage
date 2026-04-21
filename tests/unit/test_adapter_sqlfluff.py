"""SPEC-24 — unit tests for the sqlfluff adapter."""

from __future__ import annotations

import json
from pathlib import Path

from dbt_coverage.adapters import AdapterConfig, SqlfluffAdapter


def _write_report(path: Path, file_path: str) -> None:
    data = [
        {
            "filepath": file_path,
            "violations": [
                {
                    "line_no": 3,
                    "line_pos": 5,
                    "code": "L001",
                    "name": "trailing_whitespace",
                    "description": "Trailing whitespace.",
                    "warning": False,
                },
                {
                    "start_line_no": 4,
                    "start_line_pos": 2,
                    "code": "CP01",
                    "name": "capitalisation.keywords",
                    "description": "Keywords must be uppercase.",
                    "warning": False,
                },
            ],
        }
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_sqlfluff_adapter_read(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    model = tmp_path / "models" / "stg_x.sql"
    model.write_text("select 1", encoding="utf-8")

    report = tmp_path / ".dbtcov" / "sqlfluff.json"
    _write_report(report, str(model))

    adapter = SqlfluffAdapter()
    cfg = AdapterConfig()
    discovered = adapter.discover(tmp_path, cfg)
    assert discovered == report

    ar = adapter.read(report, cfg)
    assert ar.adapter == "sqlfluff"
    assert ar.invocation.status == "ok"
    assert len(ar.findings) == 2
    for f in ar.findings:
        assert f.rule_id.startswith("SQLF.")
        assert f.origins == ["sqlfluff"]
        assert not f.file_path.is_absolute()


def test_sqlfluff_adapter_missing_report(tmp_path: Path) -> None:
    adapter = SqlfluffAdapter()
    cfg = AdapterConfig()
    assert adapter.discover(tmp_path, cfg) is None
    ar = adapter.read(tmp_path / ".dbtcov" / "sqlfluff.json", cfg)
    assert ar.findings == []
    assert ar.invocation.status == "read_failed"


def test_sqlfluff_adapter_warning_downgrades() -> None:
    from dbt_coverage.adapters.sqlfluff.mapper import (
        build_severity_map,
        violation_to_finding,
    )
    from dbt_coverage.adapters.sqlfluff.parser import SqlfluffViolation
    from dbt_coverage.core import Severity

    v = SqlfluffViolation(
        file_path=Path("models/x.sql"),
        line=1,
        column=1,
        code="L004",
        name="indent.mixed",
        description="mixed indent",
        is_warning=True,
    )
    sev_map = build_severity_map({"L004": "MAJOR"})
    f = violation_to_finding(v, sev_map)
    assert f is not None
    assert f.severity is Severity.MINOR  # warning downgrades


def test_sqlfluff_adapter_absolute_path_outside_root_dropped(tmp_path: Path) -> None:
    from dbt_coverage.adapters.sqlfluff.mapper import (
        build_severity_map,
        violation_to_finding,
    )
    from dbt_coverage.adapters.sqlfluff.parser import SqlfluffViolation

    v = SqlfluffViolation(
        file_path=Path("/some/other/root/models/x.sql"),
        line=1,
        column=1,
        code="L001",
        name="trailing_whitespace",
        description="",
        is_warning=False,
    )
    f = violation_to_finding(v, build_severity_map(None), project_root=tmp_path)
    assert f is None
