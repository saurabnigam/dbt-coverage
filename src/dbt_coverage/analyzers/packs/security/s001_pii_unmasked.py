"""SPEC-30 §3 — S001: PII column projected without masking/hashing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_DEFAULT_PATTERNS = (
    r"\bssn\b",
    r"\bsocial_security\b",
    r"\btax_id\b",
    r"\bcredit_card\b",
    r"\bcc_number\b",
    r"\baadhar\b",
    r"\bpassport\b",
    r"\biban\b",
    r"\bdob\b",
    r"\bdate_of_birth\b",
    r"\bemail_address\b",
)

_DEFAULT_MASK_MACROS = ("mask_", "hash_", "redact_")


class S001PiiUnmaskedRule(BaseRule):
    id: ClassVar[str] = "S001"
    default_severity: ClassVar[Severity] = Severity.CRITICAL
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.SECURITY
    finding_type: ClassVar[FindingType] = FindingType.VULNERABILITY
    description: ClassVar[str] = "PII column projected without masking/hashing/redacting"
    confidence_base: ClassVar[float] = 0.75
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = True

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None or node.ast is None:
            return

        patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (ctx.params.get("patterns") or _DEFAULT_PATTERNS)
        ]
        mask_prefixes = tuple(
            ctx.params.get("mask_macro_prefixes") or _DEFAULT_MASK_MACROS
        )
        pii_meta = _pii_meta_columns(ctx)

        seen: set[str] = set()
        for select in node.ast.find_all(exp.Select):
            for projection in select.expressions or []:
                alias = _alias_of(projection)
                if not alias:
                    continue
                if alias.lower() in seen:
                    continue
                seen.add(alias.lower())
                if not _matches_pii(alias, patterns):
                    continue
                if _is_masked(projection, mask_prefixes):
                    continue
                if alias.lower() in pii_meta and _meta_says_masked(projection, mask_prefixes):
                    continue
                line = node.line_map.get(_line_of(projection) or 1, 1)
                yield self.make_finding(
                    ctx,
                    line=line,
                    column=1,
                    message=(
                        f"Column `{alias}` looks like PII but isn't routed through a "
                        f"mask/hash/redact macro."
                    ),
                    code_context=f"S001:{alias}",
                )
            break  # outermost projection only


def _matches_pii(name: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(name) for p in patterns)


def _is_masked(projection, mask_prefixes: tuple[str, ...]) -> bool:
    # Look for any function / anonymous call whose name starts with a masking
    # prefix. Accepts ``mask_email(x)`` and ``{{ mask_email(x) }}`` alike.
    for n in projection.walk():
        if isinstance(n, (exp.Func, exp.Anonymous)):
            name = (getattr(n, "name", "") or "").lower()
            if name.startswith(mask_prefixes):
                return True
    return False


def _meta_says_masked(projection, mask_prefixes: tuple[str, ...]) -> bool:
    return _is_masked(projection, mask_prefixes)


def _pii_meta_columns(ctx: RuleContext) -> set[str]:
    if ctx.node_id is None:
        return set()
    entry = ctx.project.models.get(ctx.node_id)
    yml = getattr(entry, "yml_meta", None) if entry else None
    if yml is None:
        return set()
    out: set[str] = set()
    for col in yml.columns or []:
        meta = getattr(col, "meta", {}) or {}
        if meta.get("pii") is True:
            out.add(col.name.lower())
    return out


def _alias_of(projection) -> str | None:
    if isinstance(projection, exp.Alias):
        return projection.alias_or_name
    if isinstance(projection, exp.Column):
        return projection.name
    return None


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
