"""SPEC-23 §5.1 / §7.1 — manifest.json loader.

Supports dbt manifest schema v10 (1.5) through v13+ (1.9+). Extracts only
what we need: test nodes (generic + singular) + unit_tests blocks (1.8+).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from dbt_coverage.adapters.errors import UnsupportedSchemaError

_LOG = logging.getLogger(__name__)

_SCHEMA_RE = re.compile(r"/v(\d+)(?:\.json)?/?$")


@dataclass(frozen=True)
class ManifestTest:
    unique_id: str
    name: str
    test_metadata_name: str | None
    namespace: str | None
    data_test_type: str  # "generic" | "singular" | "unit"
    refs: list[str] = field(default_factory=list)
    column_name: str | None = None
    file_path: Path | None = None
    # SPEC-32 §6.T003 — populated only for unit tests that are malformed (missing
    # given / expect / empty expect.rows). ``None`` when healthy or unknown.
    malformed_reason: str | None = None


@dataclass(frozen=True)
class ManifestIndex:
    dbt_version: str | None
    schema_version: int
    tests: list[ManifestTest]


def parse_manifest(path: Path) -> ManifestIndex:
    raw = _load_json(path)
    schema = _extract_schema_version(raw)
    if schema < 10:
        raise UnsupportedSchemaError(schema, tool="dbt manifest")

    metadata = raw.get("metadata") or {}
    dbt_version = metadata.get("dbt_version")

    tests: list[ManifestTest] = []

    for node in (raw.get("nodes") or {}).values():
        if (node or {}).get("resource_type") != "test":
            continue
        tests.append(_node_to_test(node))

    # dbt 1.8+ introduces unit_tests at top-level.
    for unit in (raw.get("unit_tests") or {}).values():
        tests.append(_unit_to_test(unit))

    return ManifestIndex(
        dbt_version=dbt_version,
        schema_version=schema,
        tests=tests,
    )


# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _extract_schema_version(raw: dict) -> int:
    meta = raw.get("metadata") or {}
    ver = str(meta.get("dbt_schema_version") or "")
    m = _SCHEMA_RE.search(ver)
    if m:
        return int(m.group(1))
    return 0


def _node_to_test(node: dict) -> ManifestTest:
    unique_id = str(node.get("unique_id") or "")
    name = str(node.get("name") or "")
    column_name = node.get("column_name")
    original = node.get("original_file_path") or node.get("path")
    file_path = Path(original) if original else None

    test_metadata = node.get("test_metadata") or None
    if test_metadata:
        data_test_type = "generic"
        test_metadata_name = test_metadata.get("name")
        namespace = test_metadata.get("namespace")
    else:
        data_test_type = "singular"
        test_metadata_name = None
        namespace = None

    refs = _collect_refs(node)
    return ManifestTest(
        unique_id=unique_id,
        name=name,
        test_metadata_name=test_metadata_name,
        namespace=namespace,
        data_test_type=data_test_type,
        refs=refs,
        column_name=column_name if isinstance(column_name, str) else None,
        file_path=file_path,
    )


def _unit_to_test(unit: dict) -> ManifestTest:
    unique_id = str(unit.get("unique_id") or "")
    name = str(unit.get("name") or "")
    original = unit.get("original_file_path") or unit.get("path")
    file_path = Path(original) if original else None
    refs = _collect_refs(unit)
    # Unit tests target exactly one model via `model`.
    model = unit.get("model")
    if isinstance(model, str) and model and model not in refs:
        refs.insert(0, model)
    return ManifestTest(
        unique_id=unique_id,
        name=name,
        test_metadata_name=None,
        namespace=None,
        data_test_type="unit",
        refs=refs,
        column_name=None,
        file_path=file_path,
        malformed_reason=_unit_malformed_reason(unit),
    )


def _unit_malformed_reason(unit: dict) -> str | None:
    """SPEC-32 §6.T003 — return a short diagnostic when the unit test is malformed.

    A healthy unit test has at least one ``given`` row and an ``expect`` block
    whose ``rows`` list is non-empty. Everything else is noise for the SQL
    parser and produces a silent pass today.
    """
    given = unit.get("given")
    if given is None:
        return "missing `given` block"
    if isinstance(given, list) and not given:
        return "empty `given` list"

    expect = unit.get("expect")
    if expect is None:
        return "missing `expect` block"
    if isinstance(expect, dict):
        rows = expect.get("rows")
        if rows is None:
            return "missing `expect.rows`"
        if isinstance(rows, list) and not rows:
            return "empty `expect.rows` list"
    return None


def _collect_refs(node: dict) -> list[str]:
    """Dereference `refs` + `depends_on.nodes` to a list of model unique_ids."""
    depends = ((node.get("depends_on") or {}).get("nodes") or [])
    out: list[str] = []
    seen: set[str] = set()
    for uid in depends:
        if not isinstance(uid, str):
            continue
        if uid.startswith("model.") and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out
