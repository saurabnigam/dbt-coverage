"""SPEC-07 §4.3 / SPEC-33 §4 — Engine.run() + run_with_skips()."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_coverage.core import (
    Category,
    CheckSkip,
    CheckSkipReason,
    ComplexityMetrics,
    Finding,
    FindingType,
    ParsedNode,
    Severity,
    TestResult,
    Tier,
    compute_fingerprint,
)

from .rule_base import Rule, RuleContext
from .rule_registry import RegisteredRule

if TYPE_CHECKING:
    from dbt_coverage.graph import AnalysisGraph
    from dbt_coverage.scanners import ProjectIndex

_LOG = logging.getLogger(__name__)


@dataclass
class EngineResult:
    """SPEC-33 §4 — findings + typed skip records + attempted-check counter."""

    findings: list[Finding] = field(default_factory=list)
    skips: list[CheckSkip] = field(default_factory=list)
    attempted: int = 0


class Engine:
    def __init__(
        self,
        registered_rules: list[RegisteredRule],
        graph: AnalysisGraph,
        project: ProjectIndex,
        artifacts=None,
        confidence_threshold: float = 0.7,
        complexity: dict[str, ComplexityMetrics] | None = None,
        test_results: list[TestResult] | None = None,
        dbt_version: str | None = None,
        adapter_results: dict[str, object] | None = None,
        render_mode: str | None = None,
    ) -> None:
        self.rules = registered_rules
        self.graph = graph
        self.project = project
        self.artifacts = artifacts
        self.confidence_threshold = confidence_threshold
        self.complexity = complexity or {}
        self.test_results = test_results or []
        self.dbt_version = dbt_version
        # SPEC-33 §4 — pre-dispatch gating context. ``adapter_results`` maps
        # adapter name → AdapterResult (or an object with ``.invocation.status``).
        self.adapter_results = adapter_results or {}
        self.render_mode = render_mode

    def run(self, parsed_nodes: dict[str, ParsedNode]) -> list[Finding]:
        """Back-compat: returns just the findings list."""
        return self.run_with_skips(parsed_nodes).findings

    def run_with_skips(self, parsed_nodes: dict[str, ParsedNode]) -> EngineResult:
        """SPEC-33 §4 — execute every rule, emit typed CheckSkip records."""
        findings: list[Finding] = []
        skips: list[CheckSkip] = []
        attempted = 0

        def _skip(
            rid: str,
            reason: CheckSkipReason,
            node_id: str | None = None,
            details: str | None = None,
        ) -> None:
            skips.append(
                CheckSkip(
                    rule_id=rid,
                    node_id=node_id,
                    reason=reason,
                    details=details,
                )
            )

        for rr in self.rules:
            rid = getattr(rr.rule_cls, "id", rr.rule_cls.__name__)

            # Rule-level gates — once per rule, no per-node attribution.
            if not rr.enabled:
                _skip(rid, CheckSkipReason.RULE_DISABLED)
                continue

            try:
                rule: Rule = rr.rule_cls()  # type: ignore[assignment]
            except Exception as e:
                _LOG.warning("Failed to instantiate rule %s: %s", rr.rule_cls, e)
                findings.append(self._internal_crash_finding(rr.rule_cls, None, e))
                _skip(rid, CheckSkipReason.RULE_ERROR, details=repr(e))
                continue

            required_mode = getattr(rule, "required_render_mode", None)
            if (
                required_mode is not None
                and self.render_mode is not None
                and str(required_mode) != str(self.render_mode)
            ):
                _skip(
                    rid,
                    CheckSkipReason.MODE_REQUIRED,
                    details=f"requires render_mode={required_mode!s}, got {self.render_mode!s}",
                )
                continue

            required_adapter = getattr(rule, "required_adapter", None)
            if required_adapter is not None:
                adapter_res = self.adapter_results.get(required_adapter)
                if adapter_res is None:
                    _skip(
                        rid,
                        CheckSkipReason.ADAPTER_MISSING,
                        details=f"adapter `{required_adapter}` not enabled",
                    )
                    continue
                status = getattr(getattr(adapter_res, "invocation", None), "status", "ok")
                if status not in ("ok",):
                    _skip(
                        rid,
                        CheckSkipReason.ADAPTER_FAILED,
                        details=f"adapter `{required_adapter}` status={status}",
                    )
                    continue

            if getattr(rule, "applies_to_node", True):
                for nid, node in parsed_nodes.items():
                    attempted += 1
                    if getattr(rule, "requires_ast", True):
                        if not node.parse_success:
                            _skip(rid, CheckSkipReason.PARSE_FAILED, node_id=nid)
                            continue
                        if node.render_uncertain:
                            _skip(rid, CheckSkipReason.RENDER_UNCERTAIN, node_id=nid)
                            continue
                    ctx = RuleContext(
                        node=node,
                        node_id=nid,
                        graph=self.graph,
                        project=self.project,
                        artifacts=self.artifacts,
                        params=rr.params,
                        confidence_min=rr.effective_confidence_min,
                        complexity=self.complexity,
                        test_results=self.test_results,
                        dbt_version=self.dbt_version,
                    )
                    try:
                        for f in rule.check(ctx):
                            pp = self._postprocess(f, rr)
                            if pp is not None:
                                findings.append(pp)
                    except Exception as e:
                        _LOG.warning(
                            "Rule %s crashed on node %s: %s",
                            rr.rule_cls,
                            nid,
                            e,
                            exc_info=True,
                        )
                        findings.append(
                            self._internal_crash_finding(rr.rule_cls, nid, e, node=node)
                        )
                        _skip(rid, CheckSkipReason.RULE_ERROR, node_id=nid, details=repr(e))
            else:
                attempted += 1
                ctx = RuleContext(
                    node=None,
                    node_id=None,
                    graph=self.graph,
                    project=self.project,
                    artifacts=self.artifacts,
                    params=rr.params,
                    confidence_min=rr.effective_confidence_min,
                    complexity=self.complexity,
                    test_results=self.test_results,
                    dbt_version=self.dbt_version,
                )
                try:
                    for f in rule.check(ctx):
                        pp = self._postprocess(f, rr)
                        if pp is not None:
                            findings.append(pp)
                except Exception as e:
                    _LOG.warning(
                        "Project-level rule %s crashed: %s", rr.rule_cls, e, exc_info=True
                    )
                    findings.append(self._internal_crash_finding(rr.rule_cls, None, e))
                    _skip(rid, CheckSkipReason.RULE_ERROR, details=repr(e))

        return EngineResult(
            findings=self._dedupe_and_sort(findings),
            skips=skips,
            attempted=attempted,
        )

    # ------------------------------------------------------------------ helpers

    def _postprocess(self, f: Finding, rr: RegisteredRule) -> Finding | None:
        min_conf = max(rr.effective_confidence_min, self.confidence_threshold)
        if f.confidence < min_conf:
            return None
        # Apply registered overrides (severity/tier) — the finding was built with
        # rule defaults; registered overrides may trump unless the rule already
        # passed severity/tier override kwargs.
        update: dict = {}
        if f.severity != rr.effective_severity and rr.effective_severity != getattr(
            rr.rule_cls, "default_severity", f.severity
        ):
            update["severity"] = rr.effective_severity
        if f.tier != rr.effective_tier and rr.effective_tier != getattr(
            rr.rule_cls, "default_tier", f.tier
        ):
            update["tier"] = rr.effective_tier
        if update:
            # Finding is frozen; build a copy.
            data = f.model_dump()
            data.update(update)
            return Finding(**data)
        return f

    def _internal_crash_finding(
        self,
        rule_cls: type,
        node_id: str | None,
        err: Exception,
        node: ParsedNode | None = None,
    ) -> Finding:
        rid = "INTERNAL_CRASH"
        ctx_file = (
            node.file_path if node is not None else Path("UNKNOWN")
        )
        msg = f"Rule {getattr(rule_cls, 'id', rule_cls.__name__)} crashed: {err}"
        return Finding(
            rule_id=rid,
            severity=Severity.BLOCKER,
            category=Category.GOVERNANCE,
            type=FindingType.BUG,
            tier=Tier.TIER_1_ENFORCED,
            confidence=1.0,
            message=msg,
            file_path=ctx_file if not ctx_file.is_absolute() else Path(ctx_file.name),
            line=1,
            column=1,
            node_id=node_id,
            fingerprint=compute_fingerprint(rid, str(ctx_file), msg),
        )

    def _dedupe_and_sort(self, findings: list[Finding]) -> list[Finding]:
        by_fp: dict[str, Finding] = {}
        for f in findings:
            if f.fingerprint not in by_fp:
                by_fp[f.fingerprint] = f
        out = list(by_fp.values())
        out.sort(key=lambda f: (str(f.file_path), f.line, f.rule_id))
        return out
