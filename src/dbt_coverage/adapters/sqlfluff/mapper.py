"""SPEC-24 §7 — sqlfluff violation -> Finding mapping."""

from __future__ import annotations

from pathlib import Path

from dbt_coverage.core import (
    Category,
    Finding,
    FindingType,
    Severity,
    Tier,
    compute_fingerprint,
)

from .parser import SqlfluffViolation

_DEFAULT_SEVERITY_MAP: dict[str, Severity] = {
    "L01": Severity.MINOR,
    "L02": Severity.MINOR,
    "L03": Severity.MINOR,
    "L04": Severity.MAJOR,
    "L05": Severity.MINOR,
    "L06": Severity.MINOR,
    "L07": Severity.MAJOR,
    "L08": Severity.MINOR,
    "L09": Severity.MINOR,
    "__default__": Severity.MINOR,
}


def build_severity_map(user_map: dict | None) -> dict[str, Severity]:
    """Merge user-supplied severity_map onto defaults."""
    merged: dict[str, Severity] = dict(_DEFAULT_SEVERITY_MAP)
    for k, v in (user_map or {}).items():
        if v is None:
            continue
        try:
            merged[str(k)] = Severity(str(v))
        except ValueError:
            # silently ignore unparseable values
            continue
    return merged


def violation_to_finding(
    v: SqlfluffViolation,
    severity_map: dict[str, Severity],
    *,
    project_root: Path | None = None,
) -> Finding | None:
    prefix = v.code[:3] if v.code else ""
    severity = (
        severity_map.get(v.code)
        or severity_map.get(prefix)
        or severity_map.get("__default__", Severity.MINOR)
    )
    if v.is_warning and severity in (Severity.MAJOR, Severity.CRITICAL, Severity.BLOCKER):
        severity = Severity.MINOR

    file_path = v.file_path
    if file_path.is_absolute():
        if project_root is not None:
            try:
                file_path = file_path.relative_to(project_root)
            except ValueError:
                return None
        else:
            return None

    rule_id = f"SQLF.{v.code}"
    fp = compute_fingerprint(
        rule_id=rule_id,
        file_path=str(file_path),
        code_context=f"SQLF:{v.code}:{v.name}:L{v.line}",
    )
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category=Category.QUALITY,
        type=FindingType.CODE_SMELL,
        tier=Tier.TIER_2_WARN,
        confidence=0.9,
        message=f"[{v.code} {v.name}] {v.description}".strip(),
        file_path=file_path,
        line=v.line,
        column=v.column,
        fingerprint=fp,
        origins=["sqlfluff"],
    )
