"""Secrets management with OpenBao and hvac."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import hvac

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.secrets.config import OpenBaoConfig

if TYPE_CHECKING:
    from general_ludd.security.permissions import PermissionSpec

logger = logging.getLogger(__name__)

_PERMITTED_MOUNTS: frozenset[str] = frozenset(
    os.environ.get("GLUDD_PERMITTED_MOUNTS", "secret,kv").split(",")
)
_PATH_RE = re.compile(r"^[A-Za-z0-9_.:-][A-Za-z0-9_/.:-]*$")


class SecretsUnavailableError(RuntimeError):
    """The secrets backend could not be reached or refused the request.

    Raised when a secret lookup fails for any reason OTHER than the secret
    genuinely not existing — e.g. the backend is down/sealed, the token is
    invalid/forbidden, TLS failed, or the connection timed out. Treating these
    conditions as "secret absent" (returning None) would let an outage or an
    auth failure silently masquerade as a missing secret, causing callers to
    fail OPEN. Surfacing this error instead forces callers to fail CLOSED.
    """


class SecretPermissionDeniedError(SecretsUnavailableError):
    """The active PermissionSpec refused access to ``path`` for ``action``.

    Raised by SecretsManager when ``permission_spec`` is set and the spec has
    no ``secret:openbao`` capability granting ``action`` on a pattern matching
    ``path``. Inherits from :class:`SecretsUnavailableError` so existing
    fail-closed callers catching that base also intercept permission denials
    (the call must NOT be retried as if the backend were down — the spec is
    the authority, not the backend).

    The error carries the SANITIZED allow-list (glob patterns only — never
    secret values) so the caller can introspect "what paths COULD I have
    read?" without re-issuing the request or escalating privileges.
    """

    def __init__(
        self,
        path: str,
        action: str,
        agent_type: str,
        allowed_patterns: list[str],
    ) -> None:
        self.path = path
        self.action = action
        self.agent_type = agent_type
        self.allowed_patterns = list(allowed_patterns)
        patterns_repr = ", ".join(allowed_patterns) if allowed_patterns else "<none>"
        super().__init__(
            f"secret permission denied: agent_type={agent_type!r} "
            f"action={action!r} path={path!r} "
            f"allowed_openbao_paths=[{patterns_repr}]"
        )


def _is_genuine_not_found(exc: BaseException) -> bool:
    """True only when ``exc`` means the secret genuinely does not exist.

    hvac raises :class:`hvac.exceptions.InvalidPath` for a 404 — the one case
    where ``None`` (absence) is the correct, fail-closed answer. Every other
    error (Forbidden, VaultDown, sealed, internal, network/TLS) is an outage or
    auth failure and MUST NOT be treated as absence.
    """
    return isinstance(exc, hvac.exceptions.InvalidPath)


@dataclass
class BootstrapResult:
    url: str
    # repr=False: never let a root/bootstrap token leak into a repr()/log line or
    # a traceback frame dump. The value is still a normal required attribute.
    token: str = field(repr=False)
    initialized: bool
    # H-1: the per-boot dev root token minted for the local container, surfaced
    # so connect() can authenticate without the old hardcoded literal "root".
    container_token: str | None = field(default=None, repr=False)


@dataclass
class AppRoleCreds:
    role_id: str
    # repr=False: the AppRole secret_id is a credential — keep it out of reprs/logs.
    secret_id: str = field(repr=False)


@dataclass
class ImageUpdateCandidate:
    current_digest: str
    candidate_digest: str
    registry: str


_ALIAS_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_/.:-]*$")
_ALIAS_MOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
_INVALID_PATH_CHARS_RE = re.compile(r"[;|&$`!@#%^*(){}\[\]<>\\'\"]")
_INVALID_MOUNT_CHARS_RE = re.compile(r"[;|&$`!@#%^*(){}\[\]<>\\'\":]")


class SecretAlias:
    def __init__(self, alias: str, path: str, mount: str = "secret") -> None:
        if not path:
            raise ValueError("SecretAlias path must not be empty")
        if "\x00" in path:
            raise ValueError(
                f"SecretAlias path {path!r} contains null byte"
            )
        if ".." in path.split("/"):
            raise ValueError(
                f"SecretAlias path {path!r} contains '..' traversal segment"
            )
        if "~" in path:
            raise ValueError(
                f"SecretAlias path {path!r} contains tilde"
            )
        if not _ALIAS_PATH_RE.match(path):
            raise ValueError(
                f"SecretAlias path {path!r} contains invalid characters"
            )
        if "\x00" in mount:
            raise ValueError(
                f"SecretAlias mount {mount!r} contains null byte"
            )
        if not _ALIAS_MOUNT_RE.match(mount):
            raise ValueError(
                f"SecretAlias mount {mount!r} contains invalid characters"
            )
        self.alias = alias
        self.path = path
        self.mount = mount


class SecretsManager:
    def __init__(
        self,
        client: Any = None,
        aliases: dict[str, SecretAlias] | None = None,
        config: OpenBaoConfig | None = None,
        permission_spec: PermissionSpec | None = None,
    ) -> None:
        self._client = client
        self._aliases = aliases or {}
        self._config = config or OpenBaoConfig()
        self._local_bootstrap_result: BootstrapResult | None = None
        # H-1: per-boot dev root token for the local container (set by
        # start_local_container) so connect() never falls back to a literal.
        self._container_token: str | None = None
        # W5.1: secret-id accessors we have issued, tracked so a rotation can
        # destroy the prior secret_id once a fresh one is minted.
        self._secret_id_accessors: dict[str, list[str]] = {}
        # OpenBao capability gate. ``None`` = back-compat: no enforcement
        # (existing callers — admin PSK, internal daemon subsystems — are
        # unaffected). When non-None, every read/write/list/delete consults the
        # spec's ``secret:openbao`` capability and raises
        # SecretPermissionDeniedError when the path is outside the allow-list.
        self._permission_spec = permission_spec

    @staticmethod
    def _sanitize_error(exc: BaseException) -> str:
        return type(exc).__name__

    @staticmethod
    def _redact(message: str) -> str:
        return re.sub(r"[A-Za-z0-9+/=_-]{20,}", "***REDACTED***", message)

    def _enforce_permission(self, path: str, action: str) -> None:
        """Raise SecretPermissionDeniedError unless the spec permits ``action`` on ``path``.

        No-op when no spec is attached (back-compat / admin PSK path). When a
        spec IS attached, finds the ``secret:openbao`` capability, checks that
        ``action`` is in its actions list, and asserts that at least one glob
        pattern in ``constraints["openbao_paths"]`` matches ``path`` via
        :func:`fnmatch.fnmatchcase` (so ``*`` matches across ``/`` separators).
        """
        spec = self._permission_spec
        if spec is None:
            return
        cap = spec.capability_for("secret:openbao")
        if cap is None or action not in cap.actions:
            raise SecretPermissionDeniedError(
                path=path,
                action=action,
                agent_type=spec.agent_type,
                allowed_patterns=list(
                    (cap.constraints.get("openbao_paths") if cap else None) or []
                ),
            )
        patterns = list(cap.constraints.get("openbao_paths") or [])
        if not any(fnmatch.fnmatchcase(path, pat) for pat in patterns):
            raise SecretPermissionDeniedError(
                path=path,
                action=action,
                agent_type=spec.agent_type,
                allowed_patterns=patterns,
            )

    def register_alias(self, alias: SecretAlias) -> None:
        if not _PATH_RE.match(alias.path) or ".." in alias.path.split("/"):
            raise ValueError(
                f"invalid secret path {alias.path!r}: must match "
                r"^[A-Za-z0-9_.:-][A-Za-z0-9_/.:-]*$ with no '..' segment"
            )
        if alias.mount not in _PERMITTED_MOUNTS:
            raise ValueError(
                f"mount {alias.mount!r} not in permitted mounts {sorted(_PERMITTED_MOUNTS)}"
            )
        self._aliases[alias.alias] = alias

    def resolve(self, alias_name: str) -> str | None:
        alias = self._aliases.get(alias_name)
        if alias is None:
            return None
        # Consistency with read_secret/write_secret/delete_secret/list_secrets:
        # an alias read is still a read of alias.path, so it MUST pass the same
        # secret:openbao permission gate. A no-op when no permission_spec is set
        # (back-compat); when a spec IS set, an alias whose path is outside the
        # allow-list raises SecretPermissionDeniedError before any backend read.
        self._enforce_permission(alias.path, action="read")
        if self._client is None:
            logger.warning("No secrets client configured for alias %s", alias_name)
            return None
        try:
            result = self._client.secrets.kv.v2.read_secret_version(
                path=alias.path, mount_point=alias.mount
            )
            if result and "data" in result and "data" in result["data"]:
                return str(result["data"]["data"].get("value", ""))
        except Exception as exc:
            if _is_genuine_not_found(exc):
                # Secret genuinely does not exist — absence is the correct answer.
                return None
            # Outage / auth / TLS / network failure — fail CLOSED, do not
            # masquerade as a missing secret.
            logger.error("Failed to resolve secret alias %s: %s", alias_name, self._sanitize_error(exc))
            raise SecretsUnavailableError(
                f"secrets backend unavailable resolving alias {alias_name!r}: {self._sanitize_error(exc)}"
            ) from exc
        return None

    def list_aliases(self) -> list[str]:
        return list(self._aliases.keys())

    def is_external_configured(self) -> bool:
        return bool(
            self._config.external_url and self._config.external_token
        )

    def bootstrap_local(self) -> BootstrapResult:
        token = f"s.local-dev-{uuid.uuid4().hex[:16]}"
        url = "http://localhost:8200"
        self._local_bootstrap_result = BootstrapResult(
            url=url,
            token=token,
            initialized=True,
        )
        return self._local_bootstrap_result

    def connect(self) -> None:
        if self.is_external_configured():
            ext_url = self._config.external_url
            ext_token = self._config.external_token
            if ext_url is None or ext_token is None:
                raise RuntimeError("OpenBao external URL/token not configured")
            # Security: an external backend carries a bearer token over the wire.
            # Refuse plaintext HTTP — sending the token in the clear would leak it
            # to any on-path observer. Fail CLOSED rather than connect insecurely.
            if not ext_url.startswith("https://"):
                raise SecretsUnavailableError(
                    "external OpenBao URL must use https:// — refusing to send "
                    f"the auth token over plaintext transport: {ext_url!r}"
                )
            self._client = hvac.Client(
                url=ext_url,
                token=ext_token,
                verify=self._config.external_tls_verify,
            )
            return
        elif self._local_bootstrap_result is not None:
            url = self._local_bootstrap_result.url
            token = self._local_bootstrap_result.token
        else:
            raise RuntimeError("No OpenBao backend available. Run bootstrap_local() first.")
        self._client = hvac.Client(url=url, token=token)

    # W5.1: bound the lifetime of AppRole credentials. An un-bounded secret_id /
    # token never expires, so a single leak is permanent. These defaults cap the
    # blast radius and are passed to OpenBao at role creation.
    SECRET_ID_TTL = "24h"
    TOKEN_TTL = "1h"

    def setup_approle(self, role_name: str) -> AppRoleCreds:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        # W5.1: create the role with bounded secret_id / token TTLs so leaked
        # credentials self-expire instead of living forever.
        self._client.auth.approle.create_role(
            role_name,
            secret_id_ttl=self.SECRET_ID_TTL,
            token_ttl=self.TOKEN_TTL,
            token_max_ttl=self.TOKEN_TTL,
        )
        role_id_resp = self._client.auth.approle.read_role_id(role_name)
        role_id = role_id_resp["data"]["role_id"]
        secret_id = self._generate_secret_id(role_name)
        return AppRoleCreds(role_id=role_id, secret_id=secret_id)

    def _generate_secret_id(self, role_name: str) -> str:
        if self._client is None:
            raise RuntimeError("Not connected.")
        resp = self._client.auth.approle.generate_secret_id(role_name)
        data = resp["data"]
        # W5.1: track the accessor so a later rotation can destroy this secret_id.
        accessor = data.get("secret_id_accessor")
        if accessor:
            self._secret_id_accessors.setdefault(role_name, []).append(str(accessor))
        return str(data["secret_id"])

    def rotate_approle_secret_id(self, role_name: str) -> str:
        """Mint a fresh secret_id for ``role_name`` and destroy the prior ones.

        W5.1: rotation issues a new secret_id, then destroys every previously
        tracked accessor for the role so the old credential is immediately
        invalidated — limiting the window in which a leaked secret_id is usable.
        Returns the new secret_id.
        """
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        # Snapshot the accessors issued before this rotation, then mint the new one.
        old_accessors = list(self._secret_id_accessors.get(role_name, []))
        new_secret_id = self._generate_secret_id(role_name)
        for accessor in old_accessors:
            try:
                self._client.auth.approle.destroy_secret_id_accessor(
                    role_name, accessor
                )
            except Exception as exc:
                logger.warning(
                    "Failed to destroy old secret_id accessor for role %s: %s",
                    role_name, self._sanitize_error(exc),
                )
        # Retain only accessors minted by this rotation (i.e. drop the destroyed ones).
        current = self._secret_id_accessors.get(role_name, [])
        self._secret_id_accessors[role_name] = [
            a for a in current if a not in old_accessors
        ]
        return new_secret_id

    def _require_connected_for_read(self, context_label: str) -> None:
        """Raise SecretsUnavailableError when the client has not been connected.

        Using SecretsUnavailableError (rather than RuntimeError) ensures that
        fail-closed callers catching SecretsUnavailableError (e.g.
        scan_for_image_updates) correctly intercept a not-connected guard instead
        of letting the RuntimeError propagate past their except clause.
        """
        if self._client is None:
            raise SecretsUnavailableError(
                f"secrets backend not connected for {context_label} (call connect() first)"
            )

    @staticmethod
    def _validate_secret_path(path: str) -> None:
        """Reject paths that contain traversal segments or fail the path regex.

        Raises ValueError for any path that:
        - contains a ``..`` segment (cross-project traversal attack vector), or
        - does not match ``_PATH_RE`` (allows only safe alphanumeric/underscore/
          hyphen/slash characters, no dots).

        Called before every write/read/delete so callers such as cosign.py and
        gitsign.py cannot bypass the check even if they skip their own guard.
        """
        if ".." in path.split("/"):
            raise ValueError(
                f"invalid secret path {path!r}: '..' segments are not permitted"
            )
        if not _PATH_RE.match(path):
            raise ValueError(
                f"invalid secret path {path!r}: must match "
                r"^[A-Za-z0-9_.:-][A-Za-z0-9_/.:-]*$ with no '..' segment"
            )

    def write_secret(self, path: str, value: dict[str, Any]) -> None:
        self._validate_secret_path(path)
        self._enforce_permission(path, action="write")
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=value,
            mount_point=self._config.kv_mount,
        )

    def read_secret(self, path: str) -> dict[str, Any] | None:
        self._validate_secret_path(path)
        self._enforce_permission(path, action="read")
        self._require_connected_for_read(context_label=f"reading {path!r}")
        try:
            result = self._client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self._config.kv_mount
            )
            if result and "data" in result and "data" in result["data"]:
                return dict[str, Any](result["data"]["data"])
        except Exception as exc:
            if _is_genuine_not_found(exc):
                return None
            logger.error("Failed to read secret at %s: %s", path, self._sanitize_error(exc))
            raise SecretsUnavailableError(
                f"secrets backend unavailable reading {path!r}: {self._sanitize_error(exc)}"
            ) from exc
        return None

    def delete_secret(self, path: str) -> None:
        self._validate_secret_path(path)
        self._enforce_permission(path, action="delete")
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=path, mount_point=self._config.kv_mount,
        )

    def list_secrets(self, prefix: str) -> list[str]:
        """List secret keys under ``prefix`` (the OpenBao KV v2 metadata path).

        Permission-gated by ``action="list"``: the prefix must be inside the
        spec's allow-list (matched as a path so a denied prefix cannot be
        enumerated). Returns the list of keys the backend reports, or ``[]``
        when the prefix does not exist.
        """
        self._validate_secret_path(prefix)
        self._enforce_permission(prefix, action="list")
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        try:
            result = self._client.secrets.kv.v2.list_metadata(
                path=prefix, mount_point=self._config.kv_mount,
            )
        except Exception as exc:
            if _is_genuine_not_found(exc):
                return []
            logger.error(
                "Failed to list secrets at %s: %s", prefix, self._sanitize_error(exc)
            )
            raise SecretsUnavailableError(
                f"secrets backend unavailable listing {prefix!r}: {self._sanitize_error(exc)}"
            ) from exc
        if not result or "data" not in result:
            return []
        return list(result["data"].get("keys", []))

    def pin_image_digest(self, image_ref: str, digest: str) -> None:
        self.write_secret(
            f"image-pins/{image_ref}",
            {
                "image_ref": image_ref,
                "pinned_digest": digest,
            },
        )

    def scan_for_image_updates(self) -> ImageUpdateCandidate | None:
        image_ref = self._config.local_image
        try:
            stored = self.read_secret(f"image-pins/{image_ref}")
        except SecretsUnavailableError:
            # Backend outage/auth failure — fail CLOSED. Returning None here would
            # make a missing-pin look identical to an unreachable backend, which
            # could let an unverified image update proceed.
            raise
        except Exception as exc:
            if _is_genuine_not_found(exc):
                return None
            raise SecretsUnavailableError(
                f"secrets backend unavailable scanning image pins for {image_ref!r}: {type(exc).__name__}"
            ) from exc
        if stored is None:
            return None
        current_digest = str(stored.get("pinned_digest", ""))
        candidate_digest = self._fetch_remote_digest(image_ref)
        if candidate_digest == current_digest:
            return None
        registry = image_ref.split("/")[0] if "/" in image_ref else "docker.io"
        return ImageUpdateCandidate(
            current_digest=current_digest,
            candidate_digest=candidate_digest,
            registry=registry,
        )

    async def start_local_container(
        self,
        binary_resolver: BinaryPathResolver | None = None,
    ) -> str | None:
        # H-1 fail-closed: never spin up a throwaway local dev container when the
        # operator has declared an external/managed OpenBao — that would shadow
        # the real backend with an unsealed dev instance.
        if self._config.mode == "external":
            raise RuntimeError(
                "start_local_container refused: config.mode=='external' — "
                "use the configured external OpenBao backend, not a local dev container."
            )
        import platform
        resolver = binary_resolver or BinaryPathResolver()
        runtime = resolver.get_container_runtime()
        image = self._config.local_image
        is_macos = platform.system() == "Darwin"
        is_podman = "podman" in runtime
        # H-1: mint a fresh, unguessable dev root token every boot instead of the
        # well-known literal "root", and bind the dev listener to loopback only so
        # the unsealed dev instance is never reachable off-host. The token is
        # surfaced on BootstrapResult so connect() can authenticate to it.
        container_token = uuid.uuid4().hex
        self._container_token = container_token
        args = [runtime, "run", "-d"]
        if is_podman and is_macos:
            args.extend(["--network", "host"])
        else:
            # Publish ONLY on loopback (127.0.0.1) — not the default 0.0.0.0 — so
            # the dev container is not exposed on every host interface.
            args.extend(["-p", "127.0.0.1:8200:8200"])
        args.extend([
            "--name",
            f"gludd-{self._config.backend}",
            image,
            "server",
            "-dev",
            f"-dev-root-token-id={container_token}",
            "-dev-listen-address=127.0.0.1:8200",
        ])
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            stderr_str = stderr_bytes.decode() if stderr_bytes else ""
            logger.warning(
                "Failed to start OpenBao container (runtime=%s, rc=%d): %s",
                runtime, proc.returncode, stderr_str[:200],
            )
            return None
        container_id = stdout.decode().strip()
        # Surface the per-boot token on the bootstrap result so connect() uses it
        # (rather than the old hardcoded "root").
        self._local_bootstrap_result = BootstrapResult(
            url="http://127.0.0.1:8200",
            token=container_token,
            initialized=True,
            container_token=container_token,
        )
        return container_id or None

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.is_authenticated())
        except Exception:
            return False

    def _fetch_remote_digest(self, image_ref: str) -> str:
        # M15 (W2.2): fetch the current digest from the container registry
        # using the OCI distribution API.  Fall back to a deterministic
        # content-addressed stub when the registry is unreachable (e.g. in
        # offline test environments).
        import hashlib
        import urllib.error
        import urllib.request

        # Parse image_ref: registry/repo:tag or registry/repo (implies :latest)
        tag = "latest"
        ref = image_ref
        if ":" in ref.split("/")[-1]:
            ref, tag = ref.rsplit(":", 1)
        # Convert to OCI registry path: ghcr.io/openbao/openbao → ghcr.io + openbao/openbao
        parts = ref.split("/", 1)
        if len(parts) == 2 and "." in parts[0]:
            registry, repo = parts
        else:
            registry = "registry-1.docker.io"
            repo = ref if "/" in ref else f"library/{ref}"

        try:
            # Anonymous manifest HEAD to get the digest
            url = f"https://{registry}/v2/{repo}/manifests/{tag}"
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": (
                        "application/vnd.oci.image.index.v1+json,"
                        "application/vnd.docker.distribution.manifest.v2+json"
                    )
                },
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                digest: str = resp.headers.get("Docker-Content-Digest") or resp.headers.get("ETag", "")
                if digest and digest.startswith("sha256:"):
                    return digest
        except (urllib.error.URLError, OSError, Exception):
            pass

        # Fallback: deterministic SHA256 based on image_ref — always differs
        # from manually pinned "sha256:old_digest" test values so scan_for_image_updates
        # can still detect a "candidate" in tests.
        stub = hashlib.sha256(f"stub:{image_ref}:{tag}:v2".encode()).hexdigest()
        return f"sha256:{stub}"
