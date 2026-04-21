"""SPEC-05 §5.2 — dbt mock globals for Jinja rendering (MOCK mode)."""

from __future__ import annotations

import re
from typing import Any

from .macro_indexer import MacroRegistry


class AdapterDispatchUnsupported(Exception):
    """Raised when `adapter.dispatch(...)` is invoked in MOCK mode.

    Caught by the renderer and converted to ``render_uncertain=True``.
    """


class CapturedConfig:
    """Sink for dbt ``{{ config(...) }}`` calls."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def set(self, *args: Any, **kwargs: Any) -> str:
        if args and isinstance(args[0], dict):
            self.data.update(args[0])
        self.data.update(kwargs)
        return ""

    __call__ = set


def _sanitize(name: str) -> str:
    """Make a string safe as a SQL identifier component."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


class _This:
    def __init__(self, name: str = "__THIS__") -> None:
        self._name = name
        self.schema = "public"
        self.database = "analytics"
        self.identifier = name
        self.name = name

    def __str__(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return self._name

    def include(self, **kwargs: Any) -> _This:
        return self


class _Target:
    def __init__(self, adapter: str | None) -> None:
        self.name = "dev"
        self.schema = "public"
        self.database = "analytics"
        self.type = (adapter or "postgres").lower()
        self.profile_name = "dev"
        self.threads = 1


class _Adapter:
    def dispatch(self, *args: Any, **kwargs: Any) -> Any:
        def _invoker(*a: Any, **k: Any) -> Any:
            raise AdapterDispatchUnsupported("adapter.dispatch not supported in MOCK mode")

        return _invoker

    def get_columns_in_relation(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AdapterDispatchUnsupported("adapter.get_columns_in_relation not supported in MOCK mode")


def _make_ref(captured_refs: list[str]) -> Any:
    def ref(*args: str) -> str:
        name = args[-1] if args else ""
        captured_refs.append(str(name))
        return f"__REF_{_sanitize(str(name))}__"

    return ref


def _make_source(captured_sources: list[tuple[str, str]]) -> Any:
    def source(src: str, tbl: str) -> str:
        captured_sources.append((str(src), str(tbl)))
        return f"__SRC_{_sanitize(str(src))}_{_sanitize(str(tbl))}__"

    return source


def _make_var() -> Any:
    def var(name: str, default: Any = None) -> Any:
        if default is not None:
            return str(default) if not isinstance(default, str) else default
        return f"__VAR_{_sanitize(str(name))}__"

    return var


def _make_macro_proxy(
    registry: MacroRegistry, captured_macros: list[str]
) -> Any:
    """Factory: any attribute access on this proxy returns a callable that
    captures macro name and returns a sentinel string (if known) — otherwise it
    is treated as undefined and Jinja raises at call time.
    """

    class _MacroCallable:
        def __init__(self, name: str, known: bool) -> None:
            self._name = name
            self._known = known

        def __call__(self, *args: Any, **kwargs: Any) -> str:
            if not self._known:
                # simulate UndefinedError by raising; renderer catches and marks uncertain
                raise NameError(f"unknown macro: {self._name}")
            captured_macros.append(self._name)
            return f"__MACRO_{_sanitize(self._name)}__"

    return _MacroCallable


def build_mock_context(
    macro_registry: MacroRegistry,
    captured_config: CapturedConfig,
    captured_refs: list[str],
    captured_sources: list[tuple[str, str]],
    captured_macros: list[str],
    adapter_name: str | None = None,
) -> dict[str, Any]:
    """Return the globals dict passed to each Jinja2 template render."""
    ctx: dict[str, Any] = {
        "ref": _make_ref(captured_refs),
        "source": _make_source(captured_sources),
        "config": captured_config,
        "var": _make_var(),
        "this": _This(),
        "target": _Target(adapter_name),
        "adapter": _Adapter(),
        "is_incremental": lambda: False,
        "execute": True,
        "flags": type("Flags", (), {"FULL_REFRESH": False, "STORE_FAILURES": False})(),
        "run_started_at": "2024-01-01T00:00:00Z",
        "invocation_id": "00000000-0000-0000-0000-000000000000",
    }

    # Inject known macros as top-level callables.
    callable_cls = _make_macro_proxy(macro_registry, captured_macros)
    for name in macro_registry.known_macros:
        ctx[name] = callable_cls(name, known=True)

    return ctx
