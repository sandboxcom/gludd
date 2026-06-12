"""Secrets management with OpenBao and hvac."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import hvac

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.secrets.config import OpenBaoConfig

logger = logging.getLogger(__name__)


@dataclass
class BootstrapResult:
    url: str
    token: str
    initialized: bool


@dataclass
class AppRoleCreds:
    role_id: str
    secret_id: str


@dataclass
class ImageUpdateCandidate:
    current_digest: str
    candidate_digest: str
    registry: str


class SecretAlias:
    def __init__(self, alias: str, path: str, mount: str = "secret") -> None:
        self.alias = alias
        self.path = path
        self.mount = mount


class SecretsManager:
    def __init__(
        self,
        client: Any = None,
        aliases: dict[str, SecretAlias] | None = None,
        config: OpenBaoConfig | None = None,
    ) -> None:
        self._client = client
        self._aliases = aliases or {}
        self._config = config or OpenBaoConfig()
        self._local_bootstrap_result: BootstrapResult | None = None

    def register_alias(self, alias: SecretAlias) -> None:
        self._aliases[alias.alias] = alias

    def resolve(self, alias_name: str) -> str | None:
        alias = self._aliases.get(alias_name)
        if alias is None:
            return None
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
            logger.error("Failed to resolve secret alias %s: %s", alias_name, exc)
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
            url = ext_url
            token = ext_token
        elif self._local_bootstrap_result is not None:
            url = self._local_bootstrap_result.url
            token = self._local_bootstrap_result.token
        else:
            raise RuntimeError("No OpenBao backend available. Run bootstrap_local() first.")
        self._client = hvac.Client(url=url, token=token)

    def setup_approle(self, role_name: str) -> AppRoleCreds:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.auth.approle.create_role(role_name)
        role_id_resp = self._client.auth.approle.read_role_id(role_name)
        role_id = role_id_resp["data"]["role_id"]
        secret_id = self._generate_secret_id(role_name)
        return AppRoleCreds(role_id=role_id, secret_id=secret_id)

    def _generate_secret_id(self, role_name: str) -> str:
        if self._client is None:
            raise RuntimeError("Not connected.")
        resp = self._client.auth.approle.generate_secret_id(role_name)
        return str(resp["data"]["secret_id"])

    def write_secret(self, path: str, value: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=value,
            mount_point=self._config.kv_mount,
        )

    def read_secret(self, path: str) -> dict[str, Any] | None:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        try:
            result = self._client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self._config.kv_mount
            )
            if result and "data" in result and "data" in result["data"]:
                return dict[str, Any](result["data"]["data"])
        except Exception:
            return None
        return None

    def delete_secret(self, path: str) -> None:
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=path, mount_point=self._config.kv_mount,
        )

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
        except Exception:
            return None
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
        import platform
        resolver = binary_resolver or BinaryPathResolver()
        runtime = resolver.get_container_runtime()
        image = self._config.local_image
        is_macos = platform.system() == "Darwin"
        is_podman = "podman" in runtime
        args = [runtime, "run", "-d"]
        if is_podman and is_macos:
            args.extend(["--network", "host"])
        else:
            args.extend(["-p", "8200:8200"])
        args.extend([
            "--name",
            f"gludd-{self._config.backend}",
            image,
            "server",
            "-dev",
            "-dev-root-token-id=root",
            "-dev-listen-address=0.0.0.0:8200",
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
        return container_id or None

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.is_authenticated())
        except Exception:
            return False

    def _fetch_remote_digest(self, image_ref: str) -> str:
        raise NotImplementedError(
            f"Remote digest fetch for {image_ref} is not implemented. "
            "Configure a container registry with digest API support."
        )
