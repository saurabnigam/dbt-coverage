"""dbtcov UI — web dashboard for projects, runs, and configuration."""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Lazy-import wrapper so importing the package never pulls FastAPI in."""
    from .app import create_app as _create

    return _create(*args, **kwargs)
