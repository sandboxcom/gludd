"""Provider registry with dynamic import and auto-install support."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from typing import Any

from general_ludd.models.provider_presets import PROVIDER_PRESETS
from general_ludd.schemas.todo import Todo, TodoStatus, WorkType

logger = logging.getLogger(__name__)

# Import name (underscore), NOT the pip distribution name "langchain-openai".
_DEFAULT_PROVIDER_PACKAGE = "langchain_openai"
_DEFAULT_CLASS_HINT = "ChatOpenAI"


def _profile_attr(profile: Any, name: str) -> Any:
    """Read a field from a profile that may be a dict OR a ModelProfile-like object."""
    if isinstance(profile, dict):
        return profile.get(name)
    return getattr(profile, name, None)


def _normalize_package(package: Any) -> str:
    """Coerce to a Python IMPORT name. Empty/None -> default; hyphens -> underscores."""
    if not package or not isinstance(package, str) or not package.strip():
        return _DEFAULT_PROVIDER_PACKAGE
    return package.strip().replace("-", "_")


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

    @classmethod
    def from_presets(cls) -> ProviderRegistry:
        """Build a registry populated from every entry in ``PROVIDER_PRESETS``.

        This is the auto-discovery path: every provider preset becomes a
        registered provider at startup, even if no model profile references
        it. The preset's ``provider_package`` (pip name) is normalized to the
        Python import name and ``provider_class`` becomes the class hint.
        """
        registry = cls()
        for name, preset in PROVIDER_PRESETS.items():
            package = _normalize_package(preset.get("provider_package"))
            class_hint = preset.get("provider_class") or _DEFAULT_CLASS_HINT
            registry.register_provider(name=name, package=package, class_hint=class_hint)
        return registry

    @classmethod
    def from_profiles(cls, profiles: Any) -> ProviderRegistry:
        """Build a populated ProviderRegistry from an iterable of model profiles.

        Every provider in ``PROVIDER_PRESETS`` is registered first (via
        :meth:`from_presets`) so newly added presets are reachable without an
        explicit profile. Profile-specific providers are layered on top;
        providers already registered from presets are NOT overwritten, so
        preset metadata wins for stability and de-dup is by name.

        Each profile may be a dict OR a ModelProfile-like object exposing
        ``provider``, ``provider_package`` and ``provider_class_hint``. The
        provider name defaults to ``"openai"``, the package to the IMPORT name
        ``langchain_openai`` (NOT the pip name ``langchain-openai``) and the
        class hint to ``ChatOpenAI``.

        This is the shared factory the gateways must use: every gateway built
        in src/ currently passes ``provider_registry=None``, so live model
        calls raise "No provider registry configured". Wiring this factory into
        daemon.py / worker app / models.py fixes that.
        """
        registry = cls.from_presets()
        for profile in profiles or []:
            name = _profile_attr(profile, "provider") or "openai"
            if name in registry._providers:
                # Presets win for stability; profile cannot override.
                continue
            package = _normalize_package(_profile_attr(profile, "provider_package"))
            class_hint = _profile_attr(profile, "provider_class_hint") or _DEFAULT_CLASS_HINT
            registry.register_provider(name=name, package=package, class_hint=class_hint)
        return registry

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
