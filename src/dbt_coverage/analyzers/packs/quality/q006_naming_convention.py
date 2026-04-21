"""SPEC-29 §4 — Q006: layer-to-name prefix convention."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from dbt_coverage.analyzers.rule_base import BaseRule, RuleContext
from dbt_coverage.core import Category, Finding, FindingType, Severity, Tier
from dbt_coverage.graph.layers import classify_layer
from dbt_coverage.utils.config import ArchitectureConfig

_DEFAULT_PREFIXES: dict[str, tuple[str, ...]] = {
    "staging": ("stg_",),
    "intermediate": ("int_",),
    "mart": ("fct_", "dim_"),
}


class Q006NamingConventionRule(BaseRule):
    id: ClassVar[str] = "Q006"
    default_severity: ClassVar[Severity] = Severity.MAJOR
    default_tier: ClassVar[Tier] = Tier.TIER_1_ENFORCED
    category: ClassVar[Category] = Category.QUALITY
    finding_type: ClassVar[FindingType] = FindingType.CODE_SMELL
    description: ClassVar[str] = "Model name doesn't match its layer prefix"
    confidence_base: ClassVar[float] = 0.95
    applies_to_node: ClassVar[bool] = False
    requires_ast: ClassVar[bool] = False

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        arch = _arch(ctx)
        layer_prefixes: dict[str, tuple[str, ...]] = {
            k: tuple(v) for k, v in _DEFAULT_PREFIXES.items()
        }
        overrides = ctx.params.get("layer_prefixes") or {}
        for layer, prefixes in overrides.items():
            layer_prefixes[layer] = tuple(prefixes)

        for nid, entry in ctx.project.models.items():
            file_path = str(entry.sql_file.path) if entry.sql_file else ""
            layer = classify_layer(nid, file_path, arch)
            if layer is None:
                continue
            prefixes = layer_prefixes.get(layer)
            if not prefixes:
                continue
            if entry.name.startswith(prefixes):
                continue
            path = Path(entry.sql_file.path) if entry.sql_file else Path("")
            yield self.make_finding(
                ctx,
                line=1,
                column=1,
                message=(
                    f"Model `{entry.name}` is in `{layer}` layer but doesn't start with "
                    f"{_fmt_prefixes(prefixes)}."
                ),
                code_context=f"Q006:{nid}",
                file_path_override=path,
            )


def _arch(ctx: RuleContext) -> ArchitectureConfig:
    arch = ctx.params.get("_architecture")
    if isinstance(arch, ArchitectureConfig):
        return arch
    return ArchitectureConfig()


def _fmt_prefixes(prefixes: tuple[str, ...]) -> str:
    if len(prefixes) == 1:
        return f"`{prefixes[0]}`"
    return ", ".join(f"`{p}`" for p in prefixes)
