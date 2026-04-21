"""SPEC-31 — WaiverResolver + baseline loading.

SonarQube semantics: a matched finding is not deleted; it is stamped with
``suppressed=True`` and a :class:`Suppression` block so the audit trail stays
intact. Expired overrides re-activate the finding **and** emit a
``G003 waiver expired`` governance finding.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from dbt_coverage.core import (
    Category,
    Finding,
    FindingType,
    Severity,
    Suppression,
    SuppressionSource,
    Tier,
    compute_fingerprint,
)
from dbt_coverage.utils.config import DbtcovConfig, OverrideEntry

_LOG = logging.getLogger(__name__)


@dataclass
class BaselineEntry:
    fingerprint: str
    rule_id: str | None
    node_id: str | None
    path: str | None
    reason: str = "baselined"


@dataclass
class BaselineFile:
    schema_version: int
    captured_at: str | None
    entries: list[BaselineEntry]

    @classmethod
    def load(cls, path: Path) -> BaselineFile | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _LOG.warning("Could not read baseline %s: %s", path, e)
            return None
        entries = []
        for raw in data.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            fp = raw.get("fingerprint")
            if not fp:
                continue
            entries.append(
                BaselineEntry(
                    fingerprint=str(fp),
                    rule_id=raw.get("rule_id"),
                    node_id=raw.get("node_id"),
                    path=raw.get("path"),
                    reason=str(raw.get("reason") or "baselined"),
                )
            )
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            captured_at=data.get("captured_at"),
            entries=entries,
        )


@dataclass
class _Match:
    entry: OverrideEntry
    expired: bool


class WaiverResolver:
    """Applies :class:`OverrideEntry` and baseline fingerprints to findings."""

    def __init__(
        self,
        config: DbtcovConfig,
        baseline: BaselineFile | None = None,
        today: date | None = None,
    ) -> None:
        self.config = config
        self.baseline = baseline
        self.today = today or date.today()
        self._baseline_index: dict[str, BaselineEntry] = {}
        if baseline is not None:
            self._baseline_index = {e.fingerprint: e for e in baseline.entries}

    # -------------------------------------------------------------- matching

    def _match_override(self, f: Finding) -> _Match | None:
        """Return the first override matching this finding, else None."""
        for entry in self.config.overrides:
            if not _rule_matches(entry.waive, f.rule_id):
                continue
            if not _selector_matches(entry, f):
                continue
            expired = (
                entry.expires is not None and entry.expires < self.today
            )
            return _Match(entry=entry, expired=expired)
        return None

    # -------------------------------------------------------------- API

    def apply(
        self, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]:
        """Return ``(stamped_findings, governance_extra)``.

        ``stamped_findings`` has suppressions applied. ``governance_extra``
        contains one ``G003`` finding per expired override entry.
        """
        stamped: list[Finding] = []
        expired_entries: dict[str, OverrideEntry] = {}

        for f in findings:
            match = self._match_override(f)
            if match is not None and not match.expired:
                stamped.append(_stamp_override(f, match.entry))
                continue
            if match is not None and match.expired:
                expired_entries[_entry_key(match.entry)] = match.entry
                # Expired waiver: the finding re-activates (not suppressed).
                stamped.append(f)
                continue

            # No override: try baseline.
            be = self._baseline_index.get(f.fingerprint)
            if be is not None:
                stamped.append(_stamp_baseline(f, be))
                continue

            stamped.append(f)

        extra = [
            _make_g003(entry, self.today) for entry in expired_entries.values()
        ]
        return stamped, extra


# ------------------------------------------------------------------ helpers


def _rule_matches(waive: list[str], rule_id: str) -> bool:
    for w in waive:
        w = w.strip()
        if not w:
            continue
        if w == "*":
            return True
        if w == rule_id:
            return True
        if fnmatch.fnmatch(rule_id, w):
            return True
    return False


def _selector_matches(entry: OverrideEntry, f: Finding) -> bool:
    if entry.node_ids and f.node_id and f.node_id in entry.node_ids:
        return True
    path_str = str(f.file_path)
    if entry.paths and any(fnmatch.fnmatch(path_str, p) for p in entry.paths):
        return True
    if entry.models and f.node_id:
        model_name = f.node_id.rsplit(".", 1)[-1]
        if any(fnmatch.fnmatch(model_name, m) for m in entry.models):
            return True
    return False


def _stamp_override(f: Finding, entry: OverrideEntry) -> Finding:
    supp = Suppression(
        source=SuppressionSource.OVERRIDE,
        reason=entry.reason,
        reviewer=entry.reviewer,
        expires=entry.expires,
        entry_id=entry.id,
    )
    return f.model_copy(update={"suppressed": True, "suppression": supp})


def _stamp_baseline(f: Finding, entry: BaselineEntry) -> Finding:
    supp = Suppression(
        source=SuppressionSource.BASELINE,
        reason=entry.reason,
    )
    return f.model_copy(update={"suppressed": True, "suppression": supp})


def _entry_key(entry: OverrideEntry) -> str:
    """Stable key for deduping expired entries in the emitted G003 findings."""
    if entry.id:
        return f"id:{entry.id}"
    return (
        "paths:" + ",".join(entry.paths) + "|"
        + "models:" + ",".join(entry.models) + "|"
        + "nodes:" + ",".join(entry.node_ids) + "|"
        + "rules:" + ",".join(entry.waive)
    )


def _make_g003(entry: OverrideEntry, today: date) -> Finding:
    """Synthesise a G003 finding for an expired waiver."""
    rules = ",".join(entry.waive)
    target = (
        "/".join(entry.paths or entry.models or entry.node_ids) or "<unknown>"
    )
    msg = (
        f"Waiver for [{rules}] on {target} expired on "
        f"{entry.expires.isoformat() if entry.expires else '<no date>'}; please re-review."
    )
    # Point the finding at a deterministic placeholder (dbtcov.yml).
    file_path = Path("dbtcov.yml")
    fp = compute_fingerprint("G003", _entry_key(entry), msg)
    return Finding(
        rule_id="G003",
        severity=Severity.MAJOR,
        category=Category.GOVERNANCE,
        type=FindingType.GOVERNANCE,
        tier=Tier.TIER_1_ENFORCED,
        confidence=1.0,
        message=msg,
        file_path=file_path,
        line=1,
        column=1,
        node_id=None,
        fingerprint=fp,
        origins=["waiver-resolver"],
    )


# ------------------------------------------------------------------ baseline


def load_baseline_for(
    project_root: Path,
    explicit: Path | None,
) -> BaselineFile | None:
    """Resolve the baseline path and load it (if any)."""
    candidates: list[Path] = []
    if explicit is not None:
        p = Path(explicit)
        candidates.append(p if p.is_absolute() else project_root / p)
    candidates.append(project_root / ".dbtcov" / "baseline.json")
    for c in candidates:
        if c.exists():
            return BaselineFile.load(c)
    return None


def capture_baseline(
    findings: list[Finding],
    captured_at: str | None = None,
    dbtcov_version: str | None = None,
) -> dict[str, Any]:
    """Build the in-memory dict to write to ``.dbtcov/baseline.json``."""
    return {
        "schema_version": 1,
        "captured_at": captured_at,
        "dbtcov_version": dbtcov_version,
        "entries": [
            {
                "fingerprint": f.fingerprint,
                "rule_id": f.rule_id,
                "node_id": f.node_id,
                "path": str(f.file_path),
                "reason": "baselined",
            }
            for f in findings
            if not f.suppressed
        ],
    }
