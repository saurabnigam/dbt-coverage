"""SPEC-10a §4.3 — SARIF 2.1.0 reporter."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from dbt_coverage import __version__
from dbt_coverage.analyzers.rule_registry import discover_rules
from dbt_coverage.core import ScanResult

from ._shared import severity_to_sarif_level

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)


class SARIFReporter:
    name = "sarif"
    default_filename = "findings.sarif"

    def __init__(self, skip_detail: str = "aggregated") -> None:
        # SPEC-33 §6 — ``summary`` still emits the reason breakdown in
        # ``properties.skipSummary`` but omits per-notification entries.
        self.skip_detail = (skip_detail or "aggregated").lower()

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        doc = self._build_sarif(result)
        text = json.dumps(doc, indent=2, sort_keys=False)
        if out is None:
            import sys
            sys.stdout.write(text)
            sys.stdout.write("\n")
            return
        out = _resolve_path(out, self.default_filename)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    # -------------------------------------------------------------- builders

    def _build_sarif(self, result: ScanResult) -> dict[str, Any]:
        rule_meta = self._collect_rule_metadata(result)
        rule_index = {rid: i for i, rid in enumerate(rule_meta.keys())}

        results_list: list[dict[str, Any]] = []
        for f in result.findings:
            loc = {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": _posix(f.file_path),
                        "uriBaseId": "%SRCROOT%",
                    },
                    "region": _region(f),
                }
            }
            r: dict[str, Any] = {
                "ruleId": f.rule_id,
                "level": severity_to_sarif_level(f.severity),
                "message": {"text": f.message},
                "locations": [loc],
                "partialFingerprints": {"dbtcov/v1": f.fingerprint},
                "properties": {
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "tier": f.tier.value,
                    "findingType": f.type.value,
                    "confidence": f.confidence,
                    "isNew": f.is_new,
                    "nodeId": f.node_id,
                    "origins": list(f.origins),
                },
            }
            # SPEC-31 §7.2 — SARIF native suppressions.
            if getattr(f, "suppressed", False) and f.suppression is not None:
                supp = f.suppression
                r["suppressions"] = [
                    {
                        "kind": "external",
                        "status": "accepted",
                        "justification": (
                            f"{supp.source.value}: {supp.reason}"
                            + (f" (reviewer: {supp.reviewer})" if supp.reviewer else "")
                            + (f" (expires: {supp.expires})" if supp.expires else "")
                        ),
                        "properties": {
                            "source": supp.source.value,
                            "reason": supp.reason,
                            "reviewer": supp.reviewer,
                            "expires": str(supp.expires) if supp.expires else None,
                            "appliedAt": supp.applied_at,
                        },
                    }
                ]
            if f.rule_id in rule_index:
                r["ruleIndex"] = rule_index[f.rule_id]
            results_list.append(r)

        rules_arr: list[dict[str, Any]] = []
        for rid, meta in rule_meta.items():
            rules_arr.append(
                {
                    "id": rid,
                    "name": meta["name"],
                    "shortDescription": {"text": meta["short"]},
                    "fullDescription": {"text": meta["full"]},
                    "defaultConfiguration": {"level": meta["level"]},
                    "properties": {
                        "category": meta["category"],
                        "tier": meta["tier"],
                        "findingType": meta["findingType"],
                    },
                }
            )

        root_uri = f"file://{result.project_root.as_posix()}/"
        coverage_prop = [m.model_dump() for m in result.coverage]
        render_stats = result.render_stats.model_dump()
        complexity_prop = {
            nid: m.model_dump() for nid, m in result.complexity.items()
        }
        adapter_invocations_prop = [
            inv.model_dump(mode="json") for inv in result.adapter_invocations
        ]

        # SPEC-33 §6 — skipped-check SARIF surface.
        skip_summary_prop = result.check_skip_summary.model_dump(mode="json")
        notifications = self._build_notifications(result)

        invocation: dict[str, Any] = {
            "executionSuccessful": True,
            "workingDirectory": {"uri": root_uri},
        }
        if notifications:
            invocation["toolExecutionNotifications"] = notifications

        return {
            "version": "2.1.0",
            "$schema": SARIF_SCHEMA,
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "dbtcov",
                            "version": __version__,
                            "informationUri": "https://dbtcov.dev",
                            "rules": rules_arr,
                        }
                    },
                    "results": results_list,
                    "invocations": [invocation],
                    "originalUriBaseIds": {"%SRCROOT%": {"uri": root_uri}},
                    "properties": {
                        "coverage": coverage_prop,
                        "renderStats": render_stats,
                        "dialect": result.dialect,
                        "complexity": complexity_prop,
                        "adapterInvocations": adapter_invocations_prop,
                        "renderMode": _dominant_render_mode(result),
                        "skipSummary": skip_summary_prop,
                    },
                }
            ],
        }

    def _build_notifications(self, result: ScanResult) -> list[dict[str, Any]]:
        """Build SARIF ``toolExecutionNotifications`` from check_skips.

        * ``summary``    → empty list (breakdown is in ``properties.skipSummary``).
        * ``aggregated`` → one notification per (rule_id, reason) pair.
        * ``per_pair``   → one notification per recorded skip.
        """
        from dbt_coverage.core import CheckSkipReason

        def _level(reason: CheckSkipReason) -> str:
            if reason in (CheckSkipReason.RULE_ERROR, CheckSkipReason.ADAPTER_FAILED):
                return "error"
            return "warning"

        if self.skip_detail == "summary":
            return []

        notes: list[dict[str, Any]] = []
        if self.skip_detail == "aggregated":
            for agg in result.check_skips_aggregated:
                notes.append(
                    {
                        "level": _level(agg.reason),
                        "message": {
                            "text": (
                                f"{agg.rule_id} skipped {agg.count} time(s): "
                                f"{agg.reason.value}"
                                + (f" — {agg.sample_details}" if agg.sample_details else "")
                            )
                        },
                        "properties": {
                            "ruleId": agg.rule_id,
                            "reason": agg.reason.value,
                            "count": agg.count,
                        },
                        "associatedRule": {"id": agg.rule_id},
                    }
                )
            return notes

        # per_pair
        for sk in result.check_skips:
            notes.append(
                {
                    "level": _level(sk.reason),
                    "message": {
                        "text": (
                            f"{sk.rule_id}: {sk.reason.value}"
                            + (f" — {sk.details}" if sk.details else "")
                        )
                    },
                    "properties": {
                        "ruleId": sk.rule_id,
                        "nodeId": sk.node_id,
                        "reason": sk.reason.value,
                        "details": sk.details,
                    },
                    "associatedRule": {"id": sk.rule_id},
                }
            )
        return notes

    def _collect_rule_metadata(self, result: ScanResult) -> dict[str, dict[str, Any]]:
        referenced_ids = {f.rule_id for f in result.findings}
        by_id: dict[str, dict[str, Any]] = {}
        try:
            classes = discover_rules()
            for cls in classes:
                rid = getattr(cls, "id", None)
                if not rid:
                    continue
                by_id[rid] = {
                    "name": cls.__name__,
                    "short": getattr(cls, "description", rid),
                    "full": getattr(cls, "description", rid),
                    "level": severity_to_sarif_level(
                        getattr(cls, "default_severity", None) or "MAJOR"
                    ),
                    "category": str(getattr(cls, "category", "QUALITY")),
                    "tier": str(getattr(cls, "default_tier", "TIER_2_WARN")),
                    "findingType": str(getattr(cls, "finding_type", "CODE_SMELL")),
                }
        except Exception:
            pass
        # Ensure every referenced id is present with at least minimal metadata.
        for rid in referenced_ids:
            if rid not in by_id:
                by_id[rid] = {
                    "name": rid,
                    "short": rid,
                    "full": rid,
                    "level": "warning",
                    "category": "QUALITY",
                    "tier": "TIER_2_WARN",
                    "findingType": "CODE_SMELL",
                }
        # Trim down to rules referenced by findings plus internal-crash.
        keep_ids = sorted(referenced_ids | {"INTERNAL_CRASH"} & set(by_id.keys()))
        return {rid: by_id[rid] for rid in keep_ids if rid in by_id}


def _region(f) -> dict[str, Any]:
    reg: dict[str, Any] = {"startLine": f.line, "startColumn": f.column}
    if f.end_line is not None:
        reg["endLine"] = f.end_line
    if f.end_column is not None:
        reg["endColumn"] = f.end_column
    return reg


def _posix(p: Path) -> str:
    return PurePosixPath(str(p).replace("\\", "/")).as_posix()


def _dominant_render_mode(result: ScanResult) -> str:
    rs = result.render_stats
    modes = [
        ("COMPILED", rs.rendered_compiled),
        ("MOCK", rs.rendered_mock),
        ("PARTIAL", rs.rendered_partial),
    ]
    modes.sort(key=lambda kv: kv[1], reverse=True)
    return modes[0][0] if modes and modes[0][1] else "MOCK"


def _resolve_path(out: Path, default_name: str) -> Path:
    if out.exists() and out.is_dir():
        return out / default_name
    if out.suffix == "":
        return out / default_name
    return out
