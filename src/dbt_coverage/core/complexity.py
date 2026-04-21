"""SPEC-19 §4.1 — ComplexityMetrics.

Per-model SQL + Jinja cyclomatic complexity. Stable, serializable.
Attribution fields let reporters explain *why* CC is high.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ComplexityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cc: int = Field(ge=1, description="McCabe CC; always >= 1 (base path)")

    case_arms: int = Field(default=0, ge=0)
    join_count: int = Field(default=0, ge=0)
    boolean_ops: int = Field(default=0, ge=0)
    set_op_arms: int = Field(default=0, ge=0)
    subqueries: int = Field(default=0, ge=0)
    iff_count: int = Field(default=0, ge=0)
    jinja_ifs: int = Field(default=0, ge=0)
    jinja_fors: int = Field(default=0, ge=0)

    parsed_from_ast: bool = True
    uncertain: bool = False
