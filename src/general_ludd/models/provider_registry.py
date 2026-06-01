"""Provider registry with dynamic import and auto-install support."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass

from general_ludd.schemas.todo import Todo, TodoStatus, WorkType

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    name: str
    package_name: str
    class_hint: str
    installed: bool = False


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderInfo] = {}

    def register_provider(self, name: str, package: str, class_hint: str) -> None:
        self._providers[name] = ProviderInfo(
            name=name,
            package_name=package,
            class_hint=class_hint,
        )

    def get_provider_info(self, name: str) -> ProviderInfo | None:
        return self._providers.get(name)

    def is_installed(self, provider_name: str) -> bool:
        info = self._providers.get(provider_name)
        if info is None:
            return False
        spec = importlib.util.find_spec(info.package_name)
        return spec is not None

    def install_provider(self, provider_name: str) -> Todo | None:
        if self.is_installed(provider_name):
            return None
        info = self._providers.get(provider_name)
        if info is None:
            return None
        todo = Todo(
            title=f"Install missing provider package: {info.package_name}",
            description=(
                f"Provider '{provider_name}' requires package "
                f"'{info.package_name}' (class: {info.class_hint}). "
                "Add it to pyproject.toml dependencies and run `make sync`."
            ),
            status=TodoStatus.BACKLOG,
            work_type=WorkType.DEPENDENCY,
            tags=["dependency-update", "provider", provider_name],
        )
        logger.info("Created dep-update todo for provider %s: %s", provider_name, todo.todo_id)
        return todo

    def get_provider_class(self, provider_name: str) -> type:
        info = self._providers.get(provider_name)
        if info is None:
            raise ValueError(f"Provider '{provider_name}' is not registered")
        if not self.is_installed(provider_name):
            raise ImportError(f"Provider package '{info.package_name}' is not installed")
        module = importlib.import_module(info.package_name)
        cls: type = getattr(module, info.class_hint)
        return cls

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())
