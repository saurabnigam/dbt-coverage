"""SPEC-11 §4.2 — evaluate() → GateResult."""

from __future__ import annotations

from dataclasses import dataclass, field

from dbt_coverage.core import CheckSkipReason, ScanResult, Tier

from .gate_config import GateConfig

_TIER_RANK = {Tier.TIER_1_ENFORCED: 0, Tier.TIER_2_WARN: 1}


@dataclass(frozen=True)
class GateReason:
    code: str
    message: str
    offending: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: tuple[GateReason, ...] = field(default_factory=tuple)
    counted_findings: int = 0
    suppressed_findings: int = 0


def evaluate(result: ScanResult, cfg: GateConfig) -> GateResult:
    reasons: list[GateReason] = []
    counted = 0
    suppressed = 0

    gate_rank = _TIER_RANK.get(cfg.fail_on_tier, 0)
    offending_ids: set[str] = set()

    for f in result.findings:
        # SPEC-31 §7 — waived findings never fail the gate.
        if getattr(f, "suppressed", False):
            suppressed += 1
            continue
        if cfg.fail_on_new_only and not f.is_new:
            suppressed += 1
            continue
        f_rank = _TIER_RANK.get(f.tier, 99)
        if f_rank <= gate_rank:
            counted += 1
            offending_ids.add(f.rule_id)

    if offending_ids:
        reasons.append(
            GateReason(
                code="FINDINGS_AT_OR_ABOVE_TIER",
                message=f"{counted} finding(s) at tier {cfg.fail_on_tier.value} or higher",
                offending=tuple(sorted(offending_ids)),
            )
        )

    if cfg.fail_on_coverage_regression and cfg.coverage:
        by_dim = {m.dimension: m for m in result.coverage}
        for dim in sorted(cfg.coverage.keys()):
            thresh = cfg.coverage[dim]
            metric = by_dim.get(dim)
            if metric is None:
                continue
            if metric.ratio < thresh.min:
                reasons.append(
                    GateReason(
                        code="COVERAGE_BELOW_MIN",
                        message=(
                            f"{dim} coverage {metric.ratio * 100:.0f}% "
                            f"< min {thresh.min * 100:.0f}%"
                        ),
                        offending=(dim,),
                    )
                )

    # SPEC-32 §7 — unexecuted-tests gate. Counts every TestResult from the
    # dbt-test adapter where ``executed=False`` and the underlying T001
    # finding was not suppressed via a waiver.
    max_unexec = cfg.testing.unexecuted_tests_max
    if max_unexec is not None:
        suppressed_t001 = {
            f.node_id
            for f in result.findings
            if f.rule_id == "T001" and getattr(f, "suppressed", False)
        }
        unexec = [
            tr
            for tr in result.test_results
            if not tr.executed and tr.model_unique_id not in suppressed_t001
        ]
        if len(unexec) > max_unexec:
            reasons.append(
                GateReason(
                    code="UNEXECUTED_TESTS_OVER_MAX",
                    message=(
                        f"{len(unexec)} test(s) declared but not executed "
                        f"(max {max_unexec}); attach run_results.json or add to "
                        "dbtcov.yml overrides."
                    ),
                    offending=tuple(
                        sorted({tr.test_name for tr in unexec})[:20]
                    ),
                )
            )

    # SPEC-33 §7 — skip-count thresholds. Any ``None`` threshold is skipped.
    skip_cfg = cfg.skips
    sk_summary = result.check_skip_summary
    if sk_summary and sk_summary.total_skips > 0:
        by_reason = sk_summary.by_reason

        def _enforce(reason: CheckSkipReason | None, max_val: int | None, label: str) -> None:
            if max_val is None:
                return
            actual = (
                sk_summary.total_skips if reason is None else by_reason.get(reason, 0)
            )
            if actual > max_val:
                reasons.append(
                    GateReason(
                        code="SKIPS_ABOVE_MAX",
                        message=(
                            f"{label}: {actual} skipped check(s) exceeds max {max_val}. "
                            "Enable COMPILED render mode or waive via dbtcov.yml."
                        ),
                        offending=(label,),
                    )
                )

        _enforce(CheckSkipReason.PARSE_FAILED, skip_cfg.parse_failed_max, "parse_failed")
        _enforce(
            CheckSkipReason.RENDER_UNCERTAIN,
            skip_cfg.render_uncertain_max,
            "render_uncertain",
        )
        _enforce(CheckSkipReason.RULE_ERROR, skip_cfg.rule_error_max, "rule_error")
        _enforce(
            CheckSkipReason.ADAPTER_FAILED, skip_cfg.adapter_failed_max, "adapter_failed"
        )
        _enforce(None, skip_cfg.total_max, "total_skips")

    return GateResult(
        passed=len(reasons) == 0,
        reasons=tuple(reasons),
        counted_findings=counted,
        suppressed_findings=suppressed,
    )
