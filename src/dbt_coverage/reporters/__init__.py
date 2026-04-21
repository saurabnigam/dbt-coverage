"""SPEC-10a — reporters public registry."""

from .base import Reporter
from .console import ConsoleReporter
from .json_ import JSONReporter
from .sarif import SARIFReporter

REPORTERS: dict[str, type] = {
    "json": JSONReporter,
    "sarif": SARIFReporter,
    "console": ConsoleReporter,
}

__all__ = ["Reporter", "JSONReporter", "SARIFReporter", "ConsoleReporter", "REPORTERS"]
