"""SPEC-10a §4.5 — sort/group helpers + severity styles."""

from __future__ import annotations

from dbt_coverage.core import Finding, Severity, Tier

SEVERITY_RANK: dict[Severity, int] = {
    Severity.BLOCKER: 0,
    Severity.CRITICAL: 1,
    Severity.MAJOR: 2,
    Severity.MINOR: 3,
    Severity.INFO: 4,
}

TIER_RANK: dict[Tier, int] = {
    Tier.TIER_1_ENFORCED: 0,
    Tier.TIER_2_WARN: 1,
}

SEVERITY_STYLE: dict[Severity, str] = {
    Severity.BLOCKER: "bold red",
    Severity.CRITICAL: "bold red",
    Severity.MAJOR: "yellow",
    Severity.MINOR: "dim yellow",
    Severity.INFO: "cyan",
}


def sort_findings_for_display(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            TIER_RANK.get(f.tier, 99),
            SEVERITY_RANK.get(f.severity, 99),
            str(f.file_path),
            f.line,
            f.rule_id,
        ),
    )


def group_by_tier(findings: list[Finding]) -> dict[Tier, list[Finding]]:
    out: dict[Tier, list[Finding]] = {t: [] for t in TIER_RANK}
    for f in findings:
        out.setdefault(f.tier, []).append(f)
    for k in out:
        out[k] = sort_findings_for_display(out[k])
    return out


def rule_docs_url(rule_id: str) -> str:
    return f"https://dbtcov.dev/rules/{rule_id}"


def severity_to_sarif_level(sev: Severity) -> str:
    if sev in (Severity.BLOCKER, Severity.CRITICAL):
        return "error"
    if sev in (Severity.MAJOR, Severity.MINOR):
        return "warning"
    return "note"
