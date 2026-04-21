"""SPEC-11 — quality gate public API."""

from .gate import GateReason, GateResult, evaluate
from .gate_config import CoverageThreshold, GateConfig

__all__ = ["GateConfig", "CoverageThreshold", "GateResult", "GateReason", "evaluate"]
