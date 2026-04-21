"""SPEC-30 §3 — S002: hard-coded credential/secret in SQL source."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from sqlglot import expressions as exp

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier

_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
    "stripe_live_key": re.compile(r"sk_live_[A-Za-z0-9]{24,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-\.]+"),
}
_GENERIC_HIGH_ENTROPY = re.compile(r"[A-Za-z0-9_+/=\-]{40,}")


class S002HardcodedSecretRule(BaseRule):
    id: ClassVar[str] = "S002"
    default_severity: ClassVar[Severity] = Severity.BLOCKER
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.SECURITY
    finding_type: ClassVar[FindingType] = FindingType.VULNERABILITY
    description: ClassVar[str] = "Hard-coded secret/credential detected in SQL literal"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = True
    requires_ast: ClassVar[bool] = False  # regex scan works on raw source

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        node = ctx.node
        if node is None:
            return
        include_generic = bool(ctx.params.get("include_generic_entropy", False))

        sources: list[tuple[str, int]] = []
        if node.ast is not None:
            for lit in node.ast.find_all(exp.Literal):
                if lit.is_string:
                    sources.append((lit.name, _line_of(lit) or 1))
        else:
            # Parse fallback: scan the raw source directly, using real line
            # numbers so the finding lands on the right line.
            for idx, line in enumerate(node.source_sql.splitlines(), start=1):
                sources.append((line, idx))

        seen: set[tuple[str, int]] = set()
        for text, line in sources:
            for kind, pat in _PATTERNS.items():
                m = pat.search(text or "")
                if m is None:
                    continue
                key = (kind, line)
                if key in seen:
                    continue
                seen.add(key)
                source_line = node.line_map.get(line, line)
                yield self.make_finding(
                    ctx,
                    line=source_line,
                    column=1,
                    message=(
                        f"Hard-coded secret ({kind}) detected — move to a secret manager."
                    ),
                    code_context=f"S002:{kind}",
                )
            if include_generic and _GENERIC_HIGH_ENTROPY.search(text or "") and (
                "generic",
                line,
            ) not in seen:
                seen.add(("generic", line))
                source_line = node.line_map.get(line, line)
                yield self.make_finding(
                    ctx,
                    line=source_line,
                    column=1,
                    message=(
                        "Possible hard-coded secret (high-entropy literal) — review or "
                        "move to a secret manager."
                    ),
                    code_context="S002:generic",
                    confidence=0.5,
                )


def _line_of(n) -> int | None:
    meta = getattr(n, "meta", None) or {}
    line = meta.get("line")
    return line if isinstance(line, int) else None
