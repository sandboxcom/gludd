"""LangChain Hub prompt registry as an alternative prompt source.

Provides immutable commit-based versioning, staging/production environments,
and tag-based resolution via the LangChain Hub API, with graceful degradation
to file-system templates when the Hub is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langchain import hub as langchain_hub  # type: ignore[attr-defined]

    HAS_LANGCHAIN_HUB = True
except ImportError:
    HAS_LANGCHAIN_HUB = False
    langchain_hub = None


class LangChainHubRegistry:
    """Wraps ``langchain.hub.pull`` for prompt versioning and retrieval.

    When ``use_hub`` is enabled, templates are pulled from LangChain Hub
    by namespace-qualified name (e.g. ``my-org/my-prompt``).  If the Hub is
    unreachable or ``langchain.hub`` is not installed, calls fall back to
    the local file-system registry transparently.

    Parameters
    ----------
    use_hub:
        Feature flag controlling whether Hub lookups are attempted.
    default_tag:
        Tag used for Hub resolution when the caller does not specify one
        (default ``"production"``).
    """

    def __init__(
        self,
        use_hub: bool = False,
        default_tag: str = "production",
    ) -> None:
        self.use_hub = use_hub
        self.default_tag = default_tag
        self._hub_commits: dict[str, str] = {}

    def _hub_available(self) -> bool:
        return HAS_LANGCHAIN_HUB and self.use_hub

    def load_template(self, name: str, tag: str | None = None) -> str | None:
        """Pull a template from LangChain Hub.

        Returns the raw template text on success, or ``None`` when the Hub
        is unavailable, the prompt is not found, or ``use_hub`` is ``False``.
        The caller is responsible for falling back to the file-system registry.
        """
        if not self._hub_available():
            return None

        effective_tag = tag if tag is not None else self.default_tag
        hub_path = f"{name}:{effective_tag}" if ":" not in name else name

        try:
            prompt = langchain_hub.pull(hub_path)
        except Exception:
            logger.debug("Hub lookup failed for %r", hub_path, exc_info=True)
            return None

        commit_hash: str | None = None
        if hasattr(prompt, "metadata") and isinstance(prompt.metadata, dict):
            commit_hash = prompt.metadata.get("lc_hub_commit_hash")

        if commit_hash:
            self._hub_commits[name] = commit_hash

        if hasattr(prompt, "template"):
            return str(prompt.template)

        if hasattr(prompt, "messages"):
            parts: list[str] = []
            for msg in prompt.messages:
                if hasattr(msg, "prompt") and hasattr(msg.prompt, "template"):
                    role = getattr(msg, "type", "human")
                    parts.append(f"[{role}]\n{msg.prompt.template}")
                elif hasattr(msg, "content"):
                    role = getattr(msg, "type", "human")
                    parts.append(f"[{role}]\n{msg.content}")
            if parts:
                return "\n\n".join(parts)

        logger.warning("Hub prompt %r has no extractable template text", hub_path)
        return None

    def list_hub_templates(self) -> list[str]:
        """Return template names that were successfully loaded from the Hub."""
        return list(self._hub_commits.keys())

    def get_version_info(self, name: str) -> dict[str, Any]:
        """Return version metadata for a Hub-loaded template.

        Returns the LangChain Hub commit hash (equivalent to our SHA-256
        content hash in the file-system registry).  Returns an empty dict
        when the template has not been loaded from the Hub.
        """
        commit = self._hub_commits.get(name)
        if commit is None:
            return {}
        return {
            "source": "langchain_hub",
            "hash": commit,
        }
