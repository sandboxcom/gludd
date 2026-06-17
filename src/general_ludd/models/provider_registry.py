"""Provider registry with dynamic import and auto-install support."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from dataclasses import dataclass

from general_ludd.schemas.todo import Todo, TodoStatus, WorkType

logger = logging.getLogger(__name__)

# Hardcoded allowlist of LangChain provider packages that may be dynamically
# imported.  ONLY these pip package names (normalised: dashes canonical) may
# be passed as ``provider_package`` / ``package`` values.  Any other value is
# rejected with ValueError BEFORE any importlib call, which closes the
# arbitrary-import RCE vector where a malicious config profile could set
# ``provider_package: os`` and ``provider_class_hint: system`` to execute
# arbitrary OS commands.
#
# To add a new provider: (a) add its pip package name here, (b) add it to
# pyproject.toml dependencies, (c) run ``make sync``.
PROVIDER_PACKAGE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "langchain-openai",
        "langchain-anthropic",
        "langchain-google-genai",
        "langchain-community",
        "langchain-aws",
        "langchain-ollama",
        "langchain-mistralai",
        "langchain-groq",
        "langchain-cohere",
        "langchain-fireworks",
        "langchain-together",
        "langchain-huggingface",
    }
)


def _validate_provider_package(package: str) -> None:
    """Raise ValueError if *package* is not in the provider allowlist.

    Normalises the package name (dashes ↔ underscores are interchangeable in
    pip-land; we store the dash form) before checking so that callers cannot
    bypass the check by substituting underscores.
    """
    normalised = package.replace("_", "-").lower()
    if normalised not in PROVIDER_PACKAGE_ALLOWLIST:
        raise ValueError(
            f"Provider package {package!r} is not in the allowlist. "
            f"Allowed packages: {sorted(PROVIDER_PACKAGE_ALLOWLIST)}. "
            "Add the package to PROVIDER_PACKAGE_ALLOWLIST in provider_registry.py "
            "and to pyproject.toml, then run `make sync`."
        )


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
        _validate_provider_package(package)
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
        # pip package names use dashes; Python module names use underscores.
        importable = info.package_name.replace("-", "_")
        spec = importlib.util.find_spec(importable)
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
        # Re-validate here as a defence-in-depth check: the info may have been
        # inserted via a path that bypassed register_provider (e.g. direct
        # dataclass construction or a future persistence layer).
        _validate_provider_package(info.package_name)
        if not self.is_installed(provider_name):
            raise ImportError(f"Provider package '{info.package_name}' is not installed")
        importable = info.package_name.replace("-", "_")
        module = importlib.import_module(importable)
        cls = getattr(module, info.class_hint)
        if not inspect.isclass(cls):
            raise TypeError(
                f"Provider class hint {info.class_hint!r} resolved to a non-class "
                f"object {cls!r} from package {info.package_name!r}. "
                "Set provider_class_hint to the name of the LangChain chat model class."
            )
        return cls

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())
