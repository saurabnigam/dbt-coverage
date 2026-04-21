"""SPEC-05 §5.4 — lightweight regex-based macro registry."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from dbt_coverage.scanners import ProjectIndex

_MACRO_DEF_RE = re.compile(r"{%-?\s*macro\s+(\w+)\s*\(", re.IGNORECASE)


class MacroRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    known_macros: set[str] = Field(default_factory=set)

    def is_known(self, name: str) -> bool:
        return name in self.known_macros


def index_macros(project_index: ProjectIndex) -> MacroRegistry:
    names: set[str] = set()
    for macro_file in project_index.macros:
        for match in _MACRO_DEF_RE.finditer(macro_file.content):
            names.add(match.group(1))
    return MacroRegistry(known_macros=names)
