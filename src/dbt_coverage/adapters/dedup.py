"""SPEC-21 §7 — finding dedup across adapters by fingerprint, preserving origins."""

from __future__ import annotations

from dbt_coverage.core import Finding


def merge_findings(batches: list[list[Finding]]) -> list[Finding]:
    """Merge multiple batches of findings by fingerprint.

    Two findings with the same fingerprint collapse into one, with their
    ``origins`` lists unioned (preserving order, de-duplicated). Findings
    with distinct fingerprints are kept separately.
    """
    by_fp: dict[str, Finding] = {}
    for batch in batches:
        for f in batch:
            existing = by_fp.get(f.fingerprint)
            if existing is None:
                by_fp[f.fingerprint] = f
                continue
            merged_origins = list(dict.fromkeys([*existing.origins, *f.origins]))
            if merged_origins == list(existing.origins):
                continue
            by_fp[f.fingerprint] = existing.model_copy(update={"origins": merged_origins})
    return list(by_fp.values())
