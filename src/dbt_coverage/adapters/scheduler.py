"""SPEC-21 §8 — adapter scheduler.

Runs every enabled adapter in its configured mode (read / run / auto).
Failures in any one adapter are isolated: the scan continues, and a
single ``ADAPTER_FAILED`` governance finding records what went wrong.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_coverage.core import (
    AdapterInvocation,
    AdapterMode,
    Category,
    Finding,
    FindingType,
    Severity,
    Tier,
    compute_fingerprint,
)

from .base import Adapter, AdapterConfig, AdapterResult
from .errors import AdapterNotRunnableError

if TYPE_CHECKING:
    pass

_LOG = logging.getLogger(__name__)


def run_adapters(
    project_root: Path,
    adapters: list[Adapter],
    cfg_map: dict[str, AdapterConfig],
) -> tuple[list[AdapterResult], list[AdapterInvocation]]:
    """Execute every enabled adapter; return their results + invocation log."""
    results: list[AdapterResult] = []
    invocations: list[AdapterInvocation] = []

    for adapter in sorted(adapters, key=lambda a: a.name):
        acfg = cfg_map.get(adapter.name) or AdapterConfig()
        if not acfg.enabled:
            continue

        started = time.time()
        started_ms = int(started * 1000)
        inv = AdapterInvocation(
            adapter=adapter.name,
            mode=acfg.mode,
            started_at_ms=started_ms,
            status="ok",
        )

        try:
            report = _resolve_report(adapter, project_root, acfg)
            if report is None:
                raise AdapterNotRunnableError(
                    f"{adapter.name}: no report available (mode={acfg.mode})"
                )
            ar = adapter.read(report, acfg)
            _stamp_origins(ar, adapter.name)
            inv.report_path = report
            try:
                inv.tool_version = adapter.tool_version()
            except Exception:  # pragma: no cover
                inv.tool_version = None
            # Preserve adapter-provided argv (for RUN mode bookkeeping).
            if acfg.argv:
                inv.argv = list(acfg.argv)
            results.append(ar)
        except AdapterNotRunnableError as e:
            inv.status = "not_runnable"
            inv.message = str(e)
        except subprocess.TimeoutExpired as e:
            inv.status = "timeout"
            inv.message = str(e)
            results.append(_isolated_failure(adapter.name, inv.mode, e))
        except Exception as e:  # pragma: no cover (defensive; covered via tests)
            inv.status = "run_failed" if acfg.mode == AdapterMode.RUN else "read_failed"
            inv.message = str(e)
            _LOG.warning("Adapter %s failed: %s", adapter.name, e, exc_info=True)
            results.append(_isolated_failure(adapter.name, inv.mode, e))
        finally:
            inv.duration_ms = int((time.time() - started) * 1000)
            invocations.append(inv)

    return results, invocations


def _resolve_report(
    adapter: Adapter,
    project_root: Path,
    acfg: AdapterConfig,
) -> Path | None:
    discovered = adapter.discover(project_root, acfg)

    if acfg.mode == AdapterMode.READ:
        if discovered is not None and discovered.exists():
            return discovered
        raise AdapterNotRunnableError(
            f"report not found (configured: {acfg.report}, default: {adapter.default_report_path})"
        )

    if acfg.mode == AdapterMode.RUN:
        if not adapter.is_runnable():
            raise AdapterNotRunnableError(f"{adapter.name} is not runnable in this environment")
        return adapter.run(project_root, acfg)

    # AUTO
    if discovered is not None and discovered.exists():
        return discovered
    if adapter.is_runnable():
        try:
            return adapter.run(project_root, acfg)
        except AdapterNotRunnableError:
            return None
    return None


def _stamp_origins(ar: AdapterResult, name: str) -> None:
    """Ensure every Finding surfaced by an adapter is attributed to it."""
    if not ar.findings:
        return
    new: list[Finding] = []
    for f in ar.findings:
        if f.origins and name in f.origins:
            new.append(f)
            continue
        new.append(f.model_copy(update={"origins": [name, *f.origins]}))
    ar.findings = new


def _isolated_failure(name: str, mode: AdapterMode, err: Exception) -> AdapterResult:
    """Build an AdapterResult containing a single governance finding recording the failure."""
    msg = f"Adapter {name} failed ({mode}): {err}"
    file_path = Path(".dbtcov") / f"adapter-{name}.failure"
    fp = compute_fingerprint(
        rule_id="ADAPTER_FAILED",
        file_path=str(file_path),
        code_context=f"{name}:{type(err).__name__}",
    )
    finding = Finding(
        rule_id="ADAPTER_FAILED",
        severity=Severity.MINOR,
        category=Category.GOVERNANCE,
        type=FindingType.GOVERNANCE,
        tier=Tier.TIER_2_WARN,
        confidence=1.0,
        message=msg,
        file_path=file_path,
        line=1,
        column=1,
        fingerprint=fp,
        origins=[name],
    )
    return AdapterResult(
        adapter=name,
        findings=[finding],
        invocation=AdapterInvocation(
            adapter=name,
            mode=mode,
            status="failed",
            message=msg,
        ),
    )
