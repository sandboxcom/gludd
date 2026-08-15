"""Project-scoped secret wrapper enforcing KV path containment per project."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.secrets.manager import SecretsManager


class ProjectSecretsManager:
    """Delegate secret operations to a base manager under a project-scoped prefix."""

    def __init__(
        self,
        base_manager: SecretsManager,
        project_id: str,
    ) -> None:
        """Initialize the wrapper, rejecting project ids that could escape their scope."""
        # S-1 (KV path containment): a project_id containing '/' or '..' could
        # otherwise traverse out of its own namespace
        # (projects/../<other>/secret) and read/write another project's secrets.
        # Reject those characters up front so the scoped prefix is unforgeable.
        if "/" in project_id or ".." in project_id:
            raise ValueError(f"invalid project_id {project_id!r}: must not contain '/' or '..'")
        self._base = base_manager
        self._project_id = project_id

    def _scoped_path(self, path: str) -> str:
        prefix = f"projects/{self._project_id}"
        # Normalize the combined path and assert it stays under the project's
        # own prefix — a defense-in-depth check against traversal in `path`
        # (e.g. "../../other/secret") that survives even if project_id were ever
        # permitted to vary.
        candidate = posixpath.normpath(f"{prefix}/{path}")
        if candidate != prefix and not candidate.startswith(f"{prefix}/"):
            raise ValueError(f"path {path!r} escapes project scope {prefix!r}")
        return candidate

    def write_secret(self, path: str, value: dict[str, object]) -> None:
        """Write one secret under the project's scoped KV prefix."""
        self._base.write_secret(self._scoped_path(path), value)

    def read_secret(self, path: str) -> dict[str, object] | None:
        """Read one scoped secret, or None when it does not exist."""
        result: dict[str, object] | None = self._base.read_secret(self._scoped_path(path))
        return result

    def resolve(self, alias_name: str) -> str | None:
        """Resolve an alias project-first, then via the fail-closed base resolver."""
        # S-1: project-scoped resolution. A project's secret is looked up under
        # its own scoped path first (projects/<id>/<alias>); only then do we
        # fall back to the base manager for shared credential aliases. The base
        # manager is itself fail-closed (see EnvSecretsManager.resolve), so
        # non-allowlisted ambient env vars (e.g. GLUDD_AUTH_PSK) resolve to None and
        # one project can never read another project's scoped secret by name.
        # Only KV-capable backends (OpenBao SecretsManager) support a scoped
        # read; the env-backed manager has no read_secret, so we skip straight
        # to the (fail-closed) resolve fallback there.
        if hasattr(self._base, "read_secret"):
            scoped = self.read_secret(alias_name)
            if isinstance(scoped, dict):
                value = scoped.get("value")
                if isinstance(value, str):
                    return value
        result: str | None = self._base.resolve(alias_name)
        return result

    def delete_secret(self, path: str) -> None:
        """Delete one secret from under the project's scoped KV prefix."""
        self._base.delete_secret(self._scoped_path(path))
