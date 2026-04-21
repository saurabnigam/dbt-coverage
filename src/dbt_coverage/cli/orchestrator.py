"""SPEC-12 §6.1 — pipeline orchestrator used by `scan` and `gate`."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbt_coverage.adapters import (
    AdapterConfig,
    AdapterResult,
    builtin_adapters,
    merge_findings,
    run_adapters,
)
from dbt_coverage.analyzers import (
    Engine,
    WaiverResolver,
    apply_overrides,
    discover_rules,
    load_baseline_for,
)
from dbt_coverage.complexity import compute_all as compute_complexity_all
from dbt_coverage.core import (
    AdapterInvocation,
    AggregatedCheckSkip,
    CheckSkip,
    CheckSkipReason,
    CheckSkipSummary,
    ModelSummary,
    RenderMode,
    RenderStats,
    ScanResult,
    TestResult,
)
from dbt_coverage.core.enums import Tier
from dbt_coverage.coverage import AggregatorContext, compute_all
from dbt_coverage.graph import build as build_graph
from dbt_coverage.parsers import CompiledRenderer, JinjaRenderer, SqlParser
from dbt_coverage.scanners import scan as scan_sources
from dbt_coverage.utils import (
    AdapterConfigYaml,
    DbtcovConfig,
    find_project_root,
    load_config,
    load_project_info,
    resolve_dialect,
)

_LOG = logging.getLogger(__name__)


@dataclass
class ScanBundle:
    """Everything produced by a scan — surfaced so the CLI can plumb both
    the ScanResult and the fully-merged DbtcovConfig to the gate."""

    result: ScanResult
    config: DbtcovConfig


def scan(
    path: Path,
    *,
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    project_config: Path | None = None,
    baseline_path: Path | None = None,
) -> ScanBundle:
    t0 = time.perf_counter()

    project_root = find_project_root(Path(path), project_config=project_config)
    project_info = load_project_info(project_root, project_config=project_config)
    # Source scanner operates relative to project_info.root, which may differ
    # from the initial discovery point (e.g. nested ``config/dbt_project.yml``).
    project_root = project_info.root

    config = load_config(project_root, config_path=config_path, cli_overrides=cli_overrides)

    dialect = resolve_dialect(config.dialect, project_info.adapter)

    _LOG.info("Scanning %s (dialect=%s)", project_root, dialect)
    project = scan_sources(project_info, config)

    renderer = _select_renderer(project, project_info, config, project_root)
    files_in_order = [entry.sql_file for entry in project.models.values()]
    node_ids_in_order = list(project.models.keys())
    parsed_nodes_list = renderer.render_all(files_in_order, [nid for nid in node_ids_in_order])

    parser = SqlParser(dialect)
    parsed_nodes_list = parser.parse_all(parsed_nodes_list)

    parsed_nodes: dict[str, Any] = {
        nid: node for nid, node in zip(node_ids_in_order, parsed_nodes_list, strict=False)
    }

    graph = build_graph(project, parsed_nodes, dialect=dialect)

    # SPEC-19 — per-node complexity metrics.
    complexity = compute_complexity_all(parsed_nodes)

    # SPEC-21 — run every enabled external-tool adapter.
    adapter_results, adapter_invocations = _run_all_adapters(project_root, config)
    adapter_findings = _collect_findings(adapter_results)
    test_results = _collect_test_results(adapter_results)

    rule_classes = discover_rules()
    registered = apply_overrides(rule_classes, config)

    dbt_version = _find_dbt_version(adapter_invocations)

    adapter_result_map: dict[str, object] = {ar.adapter: ar for ar in adapter_results}
    # Effective render mode = dominant mode actually applied to parsed nodes
    # (AUTO collapses to MOCK/COMPILED during rendering, so read it back here).
    effective_mode = _dominant_render_mode(parsed_nodes_list)
    engine = Engine(
        registered,
        graph=graph,
        project=project,
        artifacts=None,
        confidence_threshold=config.confidence_threshold,
        complexity=complexity,
        test_results=test_results,
        dbt_version=dbt_version,
        adapter_results=adapter_result_map,
        render_mode=effective_mode,
    )
    engine_result = engine.run_with_skips(parsed_nodes)
    rule_findings = engine_result.findings
    engine_skips = engine_result.skips
    engine_attempted = engine_result.attempted

    findings = merge_findings([rule_findings, adapter_findings])
    findings.sort(key=lambda f: (str(f.file_path), f.line, f.rule_id))

    # SPEC-31 §7 — apply waivers (dbtcov.yml overrides + baseline) before
    # coverage / score / gate see the finding list.
    baseline = load_baseline_for(project_root, baseline_path)
    resolver = WaiverResolver(config, baseline=baseline)
    findings, governance_extra = resolver.apply(findings)
    if governance_extra:
        findings.extend(governance_extra)
        findings.sort(key=lambda f: (str(f.file_path), f.line, f.rule_id))

    agg_ctx = AggregatorContext(
        project=project,
        parsed_nodes=parsed_nodes,
        complexity=complexity,
        test_results=test_results,
        config=config,
        dbt_version=dbt_version,
    )
    enabled_dims = config.coverage.dimensions or list(config.coverage.thresholds.keys()) or None
    coverage = compute_all(agg_ctx, enabled=enabled_dims)

    model_summaries = _build_model_summaries(
        project, parsed_nodes, findings, coverage, test_results, engine_skips
    )

    render_stats = RenderStats(
        total_files=len(parsed_nodes_list),
        rendered_mock=sum(1 for n in parsed_nodes_list if n.render_mode == RenderMode.MOCK),
        rendered_partial=sum(
            1 for n in parsed_nodes_list if n.render_mode == RenderMode.PARTIAL
        ),
        rendered_compiled=sum(
            1 for n in parsed_nodes_list if n.render_mode == RenderMode.COMPILED
        ),
        render_uncertain=sum(1 for n in parsed_nodes_list if n.render_uncertain),
        parse_success=sum(1 for n in parsed_nodes_list if n.parse_success),
        parse_failed=sum(1 for n in parsed_nodes_list if not n.parse_success),
    )

    duration_ms = int((time.perf_counter() - t0) * 1000)

    # SPEC-33 §3 — compute skip summary + aggregate + (optionally) per-pair.
    skip_summary, skips_aggregated, skips_per_pair = _build_skip_report(
        engine_skips, engine_attempted, config
    )

    result = ScanResult(
        findings=findings,
        coverage=coverage,
        model_summaries=model_summaries,
        project_root=project_root,
        project_name=project_info.name,
        dbt_version_detected=project_info.dbt_version_required,
        dialect=dialect,
        render_stats=render_stats,
        scan_duration_ms=duration_ms,
        complexity=complexity,
        test_results=test_results,
        adapter_invocations=adapter_invocations,
        check_skip_summary=skip_summary,
        check_skips_aggregated=skips_aggregated,
        check_skips=skips_per_pair,
    )
    return ScanBundle(result=result, config=config)


def _run_all_adapters(
    project_root: Path,
    config: DbtcovConfig,
) -> tuple[list[AdapterResult], list[AdapterInvocation]]:
    """Convert adapters YAML config → AdapterConfig map and run every adapter."""
    cfg_map: dict[str, AdapterConfig] = {}
    adapters = builtin_adapters()
    adapter_names = {a.name for a in adapters}

    for name in adapter_names:
        yaml_cfg: AdapterConfigYaml = config.adapters.get(name) or AdapterConfigYaml()
        cfg_map[name] = AdapterConfig(
            enabled=yaml_cfg.enabled,
            mode=yaml_cfg.mode,
            report=yaml_cfg.report,
            timeout_seconds=yaml_cfg.timeout_seconds,
            argv=list(yaml_cfg.argv),
            params=dict(yaml_cfg.params),
        )
    return run_adapters(project_root, adapters, cfg_map)


def _collect_findings(results: list[AdapterResult]):
    out = []
    for r in results:
        out.extend(r.findings)
    return out


def _collect_test_results(results: list[AdapterResult]) -> list[TestResult]:
    out: list[TestResult] = []
    for r in results:
        out.extend(r.test_results)
    return out


def _build_model_summaries(
    project: Any,
    parsed_nodes: dict[str, Any],
    findings: list[Any],
    coverage: list[Any],
    test_results: list[Any] | None = None,
    check_skips: list[Any] | None = None,
) -> list[ModelSummary]:
    """Build one ModelSummary per model, sorted worst-score-first."""
    # Coverage look-ups
    test_per_node: dict[str, tuple[int, int]] = {}
    doc_per_node: dict[str, tuple[int, int]] = {}
    for m in coverage:
        if m.dimension == "test":
            test_per_node = dict(m.per_node)
        elif m.dimension == "doc":
            doc_per_node = dict(m.per_node)

    # Findings grouped by node_id
    from collections import defaultdict

    tier1_by_node: dict[str, set[str]] = defaultdict(set)
    tier2_by_node: dict[str, set[str]] = defaultdict(set)
    waived_by_node: dict[str, int] = defaultdict(int)
    for f in findings:
        if f.node_id is None:
            continue
        if getattr(f, "suppressed", False):
            waived_by_node[f.node_id] += 1
            continue
        if f.tier == Tier.TIER_1_ENFORCED:
            tier1_by_node[f.node_id].add(f.rule_id)
        else:
            tier2_by_node[f.node_id].add(f.rule_id)

    # SPEC-32 §3 — test-kind + execution counts per model.
    data_count_by_node: dict[str, int] = defaultdict(int)
    unit_count_by_node: dict[str, int] = defaultdict(int)
    unexec_count_by_node: dict[str, int] = defaultdict(int)
    from dbt_coverage.core import TestKind as _TK  # local to avoid cycles

    for tr in test_results or []:
        if not tr.model_unique_id:
            continue
        if tr.kind is _TK.DATA:
            data_count_by_node[tr.model_unique_id] += 1
        elif tr.kind is _TK.UNIT:
            unit_count_by_node[tr.model_unique_id] += 1
        if not tr.executed:
            unexec_count_by_node[tr.model_unique_id] += 1

    # SPEC-33 §3 — skip count per model (rule-skip events attached to a node).
    skip_count_by_node: dict[str, int] = defaultdict(int)
    for sk in check_skips or []:
        if sk.node_id:
            skip_count_by_node[sk.node_id] += 1

    summaries: list[ModelSummary] = []
    for nid, entry in project.models.items():
        node = parsed_nodes.get(nid)
        parse_ok = node.parse_success if node else True
        uncertain = node.render_uncertain if node else False

        tc_vals = test_per_node.get(nid, (0, 1))
        test_covered = tc_vals[0] > 0

        doc_vals = doc_per_node.get(nid, (0, 1))
        doc_ratio = (doc_vals[0] / doc_vals[1]) if doc_vals[1] > 0 else 0.0

        t1 = sorted(tier1_by_node.get(nid, set()))
        t2 = sorted(tier2_by_node.get(nid, set()))

        # SPEC-26/27/28/33 — graduated 0-100 score. Each axis is bounded so a
        # single bad dimension can't drive score negative before clamping.
        score = 100
        if not test_covered:
            score -= 25
        # Smooth doc penalty: fully documented = 0 hit, 0% = -15.
        score -= int(round(max(0.0, (1.0 - doc_ratio)) * 15))
        score -= min(40, 10 * len(t1))
        score -= min(20, 3 * len(t2))
        # Unexecuted tests are a risk signal even when T001 is demoted.
        score -= min(15, 5 * unexec_count_by_node.get(nid, 0))
        if not parse_ok:
            score -= 10
        elif uncertain:
            score -= 5
        # Skipped checks indicate lost visibility, not a real defect — light
        # penalty capped to -5 so skip-heavy projects aren't nuked.
        if skip_count_by_node.get(nid, 0) > 0:
            score -= min(5, skip_count_by_node[nid])
        score = max(0, score)

        file_path = str(entry.sql_file.path) if entry.sql_file else ""
        summaries.append(
            ModelSummary(
                node_id=nid,
                name=entry.name,
                file_path=file_path,
                parse_success=parse_ok,
                render_uncertain=uncertain,
                test_covered=test_covered,
                doc_ratio=round(doc_ratio, 4),
                tier1_rules=t1,
                tier2_rules=t2,
                score=score,
                waived_count=waived_by_node.get(nid, 0),
                data_test_count=data_count_by_node.get(nid, 0),
                unit_test_count=unit_count_by_node.get(nid, 0),
                tests_not_run_count=unexec_count_by_node.get(nid, 0),
                skip_count=skip_count_by_node.get(nid, 0),
            )
        )

    summaries.sort(key=lambda s: (s.score, s.name))
    return summaries


def _dominant_render_mode(parsed_nodes_list: list) -> str:
    """Return the render mode applied to the majority of nodes.

    Used to satisfy ``Rule.required_render_mode`` pre-dispatch checks after
    ``AUTO`` mode has collapsed to MOCK/COMPILED.
    """
    counts: dict[str, int] = {}
    for n in parsed_nodes_list:
        mode_val = getattr(n.render_mode, "value", str(n.render_mode))
        counts[mode_val] = counts.get(mode_val, 0) + 1
    if not counts:
        return "MOCK"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _build_skip_report(
    skips: list[CheckSkip],
    attempted: int,
    config: DbtcovConfig,
) -> tuple[CheckSkipSummary, list[AggregatedCheckSkip], list[CheckSkip]]:
    """SPEC-33 §3 — summary is always emitted; aggregated/per_pair are opt-in."""
    total = len(skips)
    by_reason: dict[CheckSkipReason, int] = {}
    by_rule: dict[str, int] = {}
    affected_nodes: set[str] = set()
    for s in skips:
        by_reason[s.reason] = by_reason.get(s.reason, 0) + 1
        by_rule[s.rule_id] = by_rule.get(s.rule_id, 0) + 1
        if s.node_id:
            affected_nodes.add(s.node_id)
    if attempted > 0:
        effective = max(0.0, min(100.0, 100.0 * (1.0 - total / attempted)))
    else:
        effective = 100.0

    summary = CheckSkipSummary(
        total_skips=total,
        attempted_checks=attempted,
        effective_coverage_pct=round(effective, 2),
        by_reason=by_reason,
        by_rule=by_rule,
        affected_nodes=len(affected_nodes),
    )

    # Aggregation is always computed — it is cheap — but the reporter layer
    # decides whether to emit it based on ``reports.skip_detail``.
    agg: dict[tuple[str, CheckSkipReason], list[CheckSkip]] = {}
    for s in skips:
        agg.setdefault((s.rule_id, s.reason), []).append(s)
    aggregated = [
        AggregatedCheckSkip(
            rule_id=rid,
            reason=reason,
            count=len(items),
            affected_node_ids=sorted({s.node_id for s in items if s.node_id}),
            sample_details=next((s.details for s in items if s.details), None),
        )
        for (rid, reason), items in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1].value))
    ]

    # Per-pair is gated on ``reports.skip_detail=per_pair`` (or any per-reporter
    # override). The ScanResult keeps the full list — reporters down-sample.
    global_detail = (config.reports.skip_detail or "summary").lower()
    per_reporter_any = any(
        (getattr(config.reports, r, None) and getattr(config.reports, r).skip_detail == "per_pair")
        for r in ("console", "json_", "sarif")
    )
    per_pair = skips if global_detail == "per_pair" or per_reporter_any else []

    return summary, aggregated, per_pair


def _find_dbt_version(invocations: list[AdapterInvocation]) -> str | None:
    """SPEC-32 §4 — pull ``dbt_version`` from the dbt-test adapter's metadata."""
    for inv in invocations:
        if inv.adapter == "dbt-test":
            v = inv.metadata.get("dbt_version") if inv.metadata else None
            if v:
                return v
            if inv.tool_version:
                return inv.tool_version
    return None


def _select_renderer(project, project_info, config, project_root: Path):
    """SPEC-25 §4.5 — pick MOCK or COMPILED renderer based on config + availability."""
    jinja = JinjaRenderer(project, adapter_name=project_info.adapter)
    mode = config.render.mode
    compiled_dir = config.render.compiled_dir
    min_cov = config.render.compiled_min_coverage

    if mode == RenderMode.MOCK:
        return jinja

    if mode == RenderMode.COMPILED:
        return CompiledRenderer(
            project,
            project_root,
            project_info.name,
            compiled_dir=compiled_dir,
            fallback=jinja,
        )

    available, ratio = CompiledRenderer.is_available(
        project_root,
        project_info.name,
        project_index=project,
        compiled_dir=compiled_dir,
    )
    if available and ratio >= min_cov:
        _LOG.info(
            "dbtcov: selecting COMPILED renderer (hit-ratio=%.0f%%, min=%.0f%%)",
            ratio * 100,
            min_cov * 100,
        )
        return CompiledRenderer(
            project,
            project_root,
            project_info.name,
            compiled_dir=compiled_dir,
            fallback=jinja,
        )
    _LOG.info(
        "dbtcov: COMPILED unavailable (hit-ratio=%.0f%%, min=%.0f%%); using MOCK",
        ratio * 100,
        min_cov * 100,
    )
    return jinja
