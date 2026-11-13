"""Agent adapter registry.

Adapters are discovered in two ways, neither of which touches the core:

1. Built-in or in-process adapters call :func:`register_agent` (decorator).
2. Third-party packages expose an entry point in the ``ai_benchmark.agents`` group.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from importlib.metadata import entry_points
from typing import Any, TypeVar

from ai_benchmark.agents.base import AgentAdapter
from ai_benchmark.errors import AgentNotFoundError, ConfigurationError

ENTRY_POINT_GROUP = "ai_benchmark.agents"

log = logging.getLogger(__name__)

A = TypeVar("A", bound=type[AgentAdapter])


class AgentRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, type[AgentAdapter]] = {}
        self._entry_points_loaded = False

    def register(self, adapter_cls: type[AgentAdapter], *, replace: bool = False) -> None:
        name = getattr(adapter_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise ConfigurationError(f"{adapter_cls.__name__} must define a non-empty 'name'")
        existing = self._adapters.get(name)
        if existing is not None and existing is not adapter_cls and not replace:
            owner = f"{existing.__module__}.{existing.__name__}"
            raise ConfigurationError(f"agent '{name}' is already registered by {owner}")
        self._adapters[name] = adapter_cls

    def load_entry_points(self) -> None:
        """Import adapters advertised by installed packages. Broken plugins are logged."""
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                cls = ep.load()
            except Exception:  # a broken plugin must not take down the CLI
                log.exception("failed to load agent adapter entry point %r", ep.name)
                continue
            if not (isinstance(cls, type) and issubclass(cls, AgentAdapter)):
                log.error("entry point %r does not point to an AgentAdapter subclass", ep.name)
                continue
            self.register(cls)

    def names(self) -> list[str]:
        self.load_entry_points()
        return sorted(self._adapters)

    def get_class(self, name: str) -> type[AgentAdapter]:
        self.load_entry_points()
        try:
            return self._adapters[name]
        except KeyError:
            known = ", ".join(sorted(self._adapters)) or "none registered"
            raise AgentNotFoundError(f"unknown agent '{name}' (available: {known})") from None

    def create(self, name: str, options: dict[str, Any] | None = None) -> AgentAdapter:
        adapter = self.get_class(name)(options)
        adapter.validate_options(adapter.options)
        return adapter

    def __contains__(self, name: object) -> bool:
        self.load_entry_points()
        return name in self._adapters

    def __iter__(self) -> Iterator[type[AgentAdapter]]:
        self.load_entry_points()
        return iter(self._adapters[n] for n in sorted(self._adapters))


default_registry = AgentRegistry()


def register_agent(adapter_cls: A) -> A:
    """Class decorator registering an adapter in the default registry."""
    default_registry.register(adapter_cls)
    return adapter_cls
