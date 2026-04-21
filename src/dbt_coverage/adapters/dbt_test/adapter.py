"""SPEC-23 — DbtTestAdapter.

Read-only in v1: reads ``target/manifest.json`` and ``target/run_results.json``.
Emits :class:`TestResult` entries tagged with ``origin="dbt-test"``; never
shells out to ``dbt``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_coverage.adapters.base import Adapter, AdapterConfig, AdapterResult
from dbt_coverage.adapters.errors import AdapterNotRunnableError, UnsupportedSchemaError
from dbt_coverage.core import (
    AdapterInvocation,
    AdapterMode,
    TestKind,
    TestResult,
    TestStatus,
)

from .manifest import ManifestIndex, ManifestTest, parse_manifest
from .run_results import RunResultsIndex, parse_run_results


class DbtTestAdapter(Adapter):
    name: str = "dbt-test"
    display_name: str = "dbt test (manifest + run_results)"
    output_kinds: tuple[str, ...] = ("test_results",)
    default_report_path: Path | None = Path("target/run_results.json")
    default_mode = AdapterMode.AUTO

    def discover(self, project_root: Path, cfg: AdapterConfig) -> Path | None:
        run_results = self._resolve(
            project_root, cfg, key="report", default=Path("target/run_results.json")
        )
        if run_results.exists():
            return run_results
        manifest = self._resolve(
            project_root, cfg, key="manifest", default=Path("target/manifest.json")
        )
        return manifest if manifest.exists() else None

    def is_runnable(self) -> bool:
        return False  # v1: no subprocess invocation

    def run(self, project_root: Path, cfg: AdapterConfig) -> Path:
        raise AdapterNotRunnableError("dbt-test adapter is read-only in v1")

    def tool_version(self) -> str | None:  # pragma: no cover - set during read()
        return None

    def read(self, report_path: Path, cfg: AdapterConfig) -> AdapterResult:
        project_root = _infer_project_root(report_path)
        manifest_path = self._resolve(
            project_root, cfg, key="manifest", default=Path("target/manifest.json")
        )

        manifest: ManifestIndex | None = None
        run_results: RunResultsIndex | None = None
        message: str | None = None

        if manifest_path.exists():
            try:
                manifest = parse_manifest(manifest_path)
            except UnsupportedSchemaError as e:
                message = str(e)
            except Exception as e:  # pragma: no cover - defensive
                message = f"manifest.json parse failed: {e}"
                raise

        if manifest is None:
            return AdapterResult(
                adapter=self.name,
                test_results=[],
                invocation=AdapterInvocation(
                    adapter=self.name,
                    mode=cfg.mode,
                    report_path=manifest_path if manifest_path.exists() else report_path,
                    status="read_failed",
                    message=message or f"manifest.json not found at {manifest_path}",
                ),
            )

        if report_path.name == "run_results.json" and report_path.exists():
            treat_warn_as_pass = bool((cfg.params or {}).get("treat_warn_as_pass", True))
            try:
                run_results = parse_run_results(
                    report_path, treat_warn_as_pass=treat_warn_as_pass
                )
            except (UnsupportedSchemaError, Exception) as e:
                message = f"run_results.json parse failed: {e}"
                run_results = None

        test_results = _build_test_results(manifest, run_results, self.name, project_root)

        md: dict[str, str] = {}
        if manifest.dbt_version:
            md["dbt_version"] = manifest.dbt_version

        return AdapterResult(
            adapter=self.name,
            test_results=test_results,
            invocation=AdapterInvocation(
                adapter=self.name,
                mode=cfg.mode,
                tool_version=(run_results.dbt_version if run_results else manifest.dbt_version),
                report_path=report_path,
                status="ok" if message is None else "read_failed",
                message=message,
                metadata=md,
            ),
        )

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _resolve(
        project_root: Path,
        cfg: AdapterConfig,
        *,
        key: str,
        default: Path,
    ) -> Path:
        params = cfg.params or {}
        override: Any | None = params.get(key)
        if override is None and key == "report":
            override = cfg.report
        p = Path(override) if override else default
        return p if p.is_absolute() else project_root / p


def _infer_project_root(report_path: Path) -> Path:
    """report_path is typically <project_root>/target/xxx.json."""
    if report_path.name in ("run_results.json", "manifest.json"):
        parent = report_path.parent
        if parent.name == "target":
            return parent.parent
    return report_path.parent


def _build_test_results(
    manifest: ManifestIndex,
    run_results: RunResultsIndex | None,
    origin: str,
    project_root: Path,
) -> list[TestResult]:
    """SPEC-32 §4 — classify (DATA vs UNIT) and diff manifest against run_results.

    Every manifest-declared test becomes a :class:`TestResult`. When
    run_results is missing or does not contain an entry for a given
    ``unique_id``, the ``executed=False`` flag surfaces the gap instead of
    letting it disappear silently.
    """
    out: list[TestResult] = []
    for t in manifest.tests:
        status = TestStatus.UNKNOWN
        executed = False
        if run_results is not None:
            entry = run_results.results_by_unique_id.get(t.unique_id)
            if entry is not None:
                status = entry.status
                executed = True
        model_uid = t.refs[0] if t.refs else None
        out.append(
            TestResult(
                test_name=t.name,
                test_kind=_normalise_kind(t),
                model_unique_id=model_uid,
                column_name=t.column_name,
                status=status,
                file_path=_as_rel(t.file_path, project_root),
                origin=origin,
                raw_kind=t.test_metadata_name,
                kind=_classify_kind(t),
                executed=executed,
                malformed_reason=t.malformed_reason,
            )
        )
    return out


def _classify_kind(t: ManifestTest) -> TestKind:
    """Map dbt ``resource_type`` to the SPEC-32 :class:`TestKind` enum.

    ``generic`` / ``singular`` → DATA (row-level assertion).
    ``unit`` (dbt 1.8+) → UNIT (mocked model I/O).
    """
    if t.data_test_type in ("generic", "singular"):
        return TestKind.DATA
    if t.data_test_type == "unit":
        return TestKind.UNIT
    return TestKind.UNKNOWN


def _normalise_kind(t: ManifestTest) -> str:
    if t.data_test_type == "singular":
        return "singular"
    if t.data_test_type == "unit":
        return "unit_test"
    if t.namespace and t.test_metadata_name:
        return f"{t.namespace}.{t.test_metadata_name}"
    return t.test_metadata_name or "unknown"


def _as_rel(path: Path | None, project_root: Path) -> Path | None:
    if path is None:
        return None
    if not path.is_absolute():
        return path
    try:
        return path.relative_to(project_root)
    except ValueError:
        return None
