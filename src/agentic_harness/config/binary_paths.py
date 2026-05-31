"""Configurable paths for all external binaries."""

from __future__ import annotations

import shutil

from pydantic import BaseModel


class BinaryPaths(BaseModel):
    terraform: str = "terraform"
    opentofu: str = "tofu"
    vault: str = "vault"
    openbao: str = "bao"
    podman: str = "podman"
    docker: str = "docker"
    ansible_playbook: str = "ansible-playbook"
    git: str = "git"
    uv: str = "uv"


class BinaryPathResolver:
    def __init__(self, config: BinaryPaths | None = None) -> None:
        self._config = config or BinaryPaths()

    def resolve(self, binary_name: str) -> str:
        configured: str | None = getattr(self._config, binary_name, None)
        if configured is None:
            found = shutil.which(binary_name)
            return found or binary_name
        if "/" in configured:
            return str(configured)
        found = shutil.which(configured)
        return found or str(configured)

    def is_available(self, binary_name: str) -> bool:
        configured: str | None = getattr(self._config, binary_name, None)
        if configured is None:
            return shutil.which(binary_name) is not None
        if "/" in configured:
            return True
        return shutil.which(configured) is not None

    def get_infra_binary(self) -> str:
        if self.is_available("opentofu"):
            return self._config.opentofu
        return self._config.terraform

    def get_secrets_binary(self) -> str:
        if self.is_available("openbao"):
            return self._config.openbao
        return self._config.vault

    def get_container_runtime(self) -> str:
        if self.is_available("podman"):
            return self._config.podman
        return self._config.docker
