"""SPEC-10a §4.4 — Console reporter using rich."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table
from rich.text import Text

from dbt_coverage.core import ScanResult, TestKind, TestStatus, Tier

from ._shared import SEVERITY_STYLE, TIER_RANK, group_by_tier

if TYPE_CHECKING:
    from dbt_coverage.quality_gates import GateConfig


class ConsoleReporter:
    name = "console"
    default_filename = None

    def __init__(
        self,
        gate_config: GateConfig | None = None,
        use_color: bool = True,
        show_suppressed: bool = False,
        skip_detail: str = "summary",
    ) -> None:
        self.gate_config = gate_config
        self.use_color = use_color
        self.show_suppressed = show_suppressed
        # SPEC-33 §6 — "summary" | "aggregated" | "per_pair". The console
        # defaults to ``summary`` even when the global/report-level detail is
        # higher, since CI logs are noisy enough.
        self.skip_detail = (skip_detail or "summary").lower()

    def emit(self, result: ScanResult, out: Path | None = None) -> None:
        if out is not None:
            with open(out, "w", encoding="utf-8") as fh:
                console = Console(
                    file=fh,
                    color_system=None,
                    force_terminal=False,
                    no_color=True,
                    highlight=False,
                )
                self._render(result, console)
            return
        console = Console(
            color_system="auto" if self.use_color else None,
            no_color=not self.use_color,
            highlight=False,
        )
        self._render(result, console)

    def _render(self, result: ScanResult, console: Console) -> None:
        if not result.findings and not result.coverage:
            console.print("No findings. Coverage not computed.")
            return

        header = Text()
        header.append("dbt project: ", style="bold")
        header.append(f"{result.project_name or 'unknown'} ")
        header.append(f"({result.dialect})", style="dim")
        console.print(header)

        rs = result.render_stats
        # SPEC-25 §4.8 — describe the dominant render mode.
        mode_parts = []
        if rs.rendered_compiled:
            mode_parts.append(f"COMPILED={rs.rendered_compiled}")
        if rs.rendered_mock:
            mode_parts.append(f"MOCK={rs.rendered_mock}")
        if rs.rendered_partial:
            mode_parts.append(f"PARTIAL={rs.rendered_partial}")
        mode_str = "  ".join(mode_parts) if mode_parts else "MOCK=0"
        console.print(
            f"render: {mode_str}  files={rs.total_files}  parsed={rs.parse_success}  "
            f"uncertain={rs.render_uncertain}  parse_failed={rs.parse_failed}",
            style="dim",
        )
        if rs.rendered_compiled:
            console.print(
                "note: line numbers reference compiled SQL (target/compiled); "
                "source-line mapping is v2.",
                style="dim",
            )
        console.rule(style="dim")

        if result.coverage:
            console.print("[bold]Coverage[/bold]")
            cov_tbl = Table(show_header=False, box=None, padding=(0, 1))
            for m in result.coverage:
                gate_str = ""
                color = "default"
                if self.gate_config is not None:
                    thresh = self.gate_config.coverage.get(m.dimension)
                    if thresh is not None:
                        if m.ratio >= thresh.min:
                            gate_str = f"(≥ {thresh.min * 100:.0f}%)"
                            color = "green"
                        else:
                            gate_str = f"(< {thresh.min * 100:.0f}%)"
                            color = "red"
                ratio_pct = f"{m.ratio * 100:.0f}%" if m.total > 0 else "N/A"
                cov_tbl.add_row(
                    m.dimension,
                    f"{m.covered}/{m.total}",
                    Text(ratio_pct, style=color),
                    gate_str,
                )
            console.print(cov_tbl)

        if result.complexity:
            self._render_complexity(result, console)

        # SPEC-33 §6 — "Skipped checks" section (banner + optional table).
        if result.check_skip_summary and result.check_skip_summary.total_skips > 0:
            self._render_skips(result, console)

        # SPEC-31 §7.2 — partition visible vs suppressed findings.
        if self.show_suppressed:
            displayable = result.findings
        else:
            displayable = [
                f for f in result.findings if not getattr(f, "suppressed", False)
            ]
        suppressed_count = sum(
            1 for f in result.findings if getattr(f, "suppressed", False)
        )
        if suppressed_count and not self.show_suppressed:
            console.print(
                f"\n[dim]Suppressed: {suppressed_count} finding(s) hidden by "
                "dbtcov.yml overrides / baseline — rerun with --show-suppressed "
                "to view.[/dim]"
            )

        groups = group_by_tier(displayable)
        for tier in sorted(groups.keys(), key=lambda t: TIER_RANK.get(t, 99)):
            findings = groups[tier]
            if not findings:
                continue
            label = (
                "Tier-1 (gate-blocking)"
                if tier == Tier.TIER_1_ENFORCED
                else "Tier-2 (warn)"
            )
            console.print(f"\n[bold]{label}[/bold]  {len(findings)} finding(s)")
            tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
            tbl.add_column("SEV")
            tbl.add_column("ID")
            tbl.add_column("RULE / MESSAGE")
            tbl.add_column("LOCATION", overflow="fold")
            tbl.add_column("CONF", justify="right")
            if self.show_suppressed:
                tbl.add_column("WAIVED", justify="center")
            for f in findings[:100]:
                sev_style = SEVERITY_STYLE.get(f.severity, "default")
                loc = f"{f.file_path}:{f.line}"
                row = [
                    Text(str(f.severity.value), style=sev_style),
                    f.rule_id,
                    f.message,
                    loc,
                    f"{f.confidence:.2f}",
                ]
                if self.show_suppressed:
                    if getattr(f, "suppressed", False) and f.suppression is not None:
                        src = f.suppression.source.value
                        row.append(Text(src, style="dim italic"))
                    else:
                        row.append("")
                tbl.add_row(*row)
            if len(findings) > 100:
                pad = "" if not self.show_suppressed else ""
                extra = [
                    "",
                    "",
                    f"(+{len(findings) - 100} more — see JSON report)",
                    "",
                    "",
                ]
                if self.show_suppressed:
                    extra.append(pad)
                tbl.add_row(*extra)
            console.print(tbl)

        if result.test_results:
            self._render_test_summary(result, console)

        if result.adapter_invocations:
            self._render_adapters(result, console)

        console.rule(style="dim")

    # ------------------------------------------------------------------

    def _render_complexity(self, result: ScanResult, console: Console) -> None:
        values = [(nid, m.cc) for nid, m in result.complexity.items()]
        if not values:
            return
        values.sort(key=lambda kv: kv[1], reverse=True)
        ccs = [v for _, v in values]
        total_cc = sum(ccs)
        avg_cc = total_cc / len(ccs) if ccs else 0.0
        max_cc = ccs[0] if ccs else 0

        console.print(
            f"\n[bold]Complexity[/bold]  models={len(ccs)}  "
            f"avg_cc={avg_cc:.1f}  max_cc={max_cc}  total_cc={total_cc}"
        )
        tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        tbl.add_column("MODEL")
        tbl.add_column("CC", justify="right")
        tbl.add_column("AST?", justify="center")
        for nid, cc in values[:10]:
            entry = result.complexity[nid]
            tbl.add_row(
                nid,
                str(cc),
                "y" if entry.parsed_from_ast else "n",
            )
        if len(values) > 10:
            tbl.add_row("", "", f"(+{len(values) - 10} more)")
        console.print(tbl)

    def _render_skips(self, result: ScanResult, console: Console) -> None:
        """SPEC-33 §6 — skipped-check banner + (optionally) aggregated table."""
        summary = result.check_skip_summary
        eff = summary.effective_coverage_pct

        # Banner. Turns red when a RULE_ERROR or ADAPTER_FAILED appeared, since
        # those reasons fail the gate by default.
        from dbt_coverage.core import CheckSkipReason as _CSR

        severe = summary.by_reason.get(_CSR.RULE_ERROR, 0) + summary.by_reason.get(
            _CSR.ADAPTER_FAILED, 0
        )
        head_style = "red" if severe else "yellow"

        console.print(
            f"\n[bold {head_style}]Skipped checks[/bold {head_style}]  "
            f"total={summary.total_skips}  "
            f"attempted={summary.attempted_checks}  "
            f"effective={eff:.1f}%",
        )
        if severe:
            console.print(
                f"[red]⚠ {severe} skip(s) from RULE_ERROR / ADAPTER_FAILED "
                "— these will fail the gate unless waived.[/red]"
            )

        # Reason breakdown is always shown (it's already summarised — 1 line).
        reason_tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        reason_tbl.add_column("REASON")
        reason_tbl.add_column("COUNT", justify="right")
        for reason, cnt in sorted(
            summary.by_reason.items(), key=lambda kv: kv[1], reverse=True
        ):
            style = "red" if reason in (_CSR.RULE_ERROR, _CSR.ADAPTER_FAILED) else "dim"
            reason_tbl.add_row(
                Text(reason.value, style=style),
                Text(str(cnt), style=style),
            )
        console.print(reason_tbl)

        if self.skip_detail in ("aggregated", "per_pair") and result.check_skips_aggregated:
            agg_tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
            agg_tbl.add_column("RULE")
            agg_tbl.add_column("REASON")
            agg_tbl.add_column("COUNT", justify="right")
            agg_tbl.add_column("SAMPLE", overflow="fold")
            for entry in result.check_skips_aggregated[:30]:
                agg_tbl.add_row(
                    entry.rule_id,
                    entry.reason.value,
                    str(entry.count),
                    entry.sample_details or "",
                )
            if len(result.check_skips_aggregated) > 30:
                agg_tbl.add_row(
                    "",
                    "",
                    "",
                    f"(+{len(result.check_skips_aggregated) - 30} more — see JSON)",
                )
            console.print(agg_tbl)

    def _render_test_summary(self, result: ScanResult, console: Console) -> None:
        """SPEC-32 §8 — 2×N grid: rows = PASS/FAIL/ERROR/SKIP/NOT_EXECUTED, cols = DATA|UNIT."""
        rows = [
            ("PASS", TestStatus.PASS),
            ("FAIL", TestStatus.FAIL),
            ("ERROR", TestStatus.ERROR),
            ("SKIP", TestStatus.SKIPPED),
        ]

        def _count(kind: TestKind, status: TestStatus) -> int:
            return sum(
                1
                for tr in result.test_results
                if tr.kind is kind and tr.executed and tr.status is status
            )

        def _unexec(kind: TestKind) -> int:
            return sum(
                1 for tr in result.test_results if tr.kind is kind and not tr.executed
            )

        total_data = sum(1 for tr in result.test_results if tr.kind is TestKind.DATA)
        total_unit = sum(1 for tr in result.test_results if tr.kind is TestKind.UNIT)

        console.print(
            f"\n[bold]Tests[/bold]  data={total_data}  unit={total_unit}"
        )
        tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        tbl.add_column("STATUS")
        tbl.add_column("DATA", justify="right")
        tbl.add_column("UNIT", justify="right")
        for label, status in rows:
            tbl.add_row(
                label,
                str(_count(TestKind.DATA, status)),
                str(_count(TestKind.UNIT, status)),
            )
        ne_data = _unexec(TestKind.DATA)
        ne_unit = _unexec(TestKind.UNIT)
        ne_style = "red" if (ne_data + ne_unit) > 0 else "default"
        tbl.add_row(
            Text("NOT_EXECUTED", style=ne_style),
            Text(str(ne_data), style=ne_style),
            Text(str(ne_unit), style=ne_style),
        )
        console.print(tbl)

    def _render_adapters(self, result: ScanResult, console: Console) -> None:
        console.print("\n[bold]Adapters[/bold]")
        tbl = Table(show_header=True, header_style="bold dim", padding=(0, 1))
        tbl.add_column("NAME")
        tbl.add_column("MODE")
        tbl.add_column("STATUS")
        tbl.add_column("REPORT/NOTE", overflow="fold")
        for inv in result.adapter_invocations:
            note = str(inv.report_path) if inv.report_path else (inv.message or "")
            tbl.add_row(
                inv.adapter,
                inv.mode.value,
                inv.status,
                note,
            )
        console.print(tbl)
