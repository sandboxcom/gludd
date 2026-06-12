"""Terraform/OpenTofu deployment lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from typing import Any, Protocol

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.infra.compute import ComputeConfig, ComputeInstance
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.schemas.deployment import DeploymentRecord

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "deployments.json"


class SecretsResolver(Protocol):
    def resolve(self, alias_name: str) -> str | None: ...


class DeploymentManager:
    def __init__(
        self,
        binary_paths: BinaryPathResolver | None = None,
        working_dir: str | None = None,
        secrets_resolver: SecretsResolver | None = None,
    ) -> None:
        self._binary_resolver = binary_paths or BinaryPathResolver()
        self._working_dir = working_dir or tempfile.mkdtemp(prefix="gludd-tf-")
        # The dir the NEXT terraform invocation runs in. deploy()/destroy() point
        # this at the per-instance dir so one manager can hold many deployments.
        self._active_working_dir = self._working_dir
        self._generator = TerraformGenerator()
        self._secrets_resolver = secrets_resolver
        self._last_config: ComputeConfig | None = None
        # W2.3 (C5/M2): instance_id -> DeploymentRecord, persisted to disk so a
        # restart still knows what is deployed and where (deploy-before-destroy).
        self._registry: dict[str, DeploymentRecord] = {}
        self._load_registry()

    @property
    def _registry_path(self) -> str:
        return os.path.join(self._working_dir, _REGISTRY_FILE)

    def _load_registry(self) -> None:
        path = self._registry_path
        if not os.path.isfile(path):
            return
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        for inst_id, data in (raw or {}).items():
            try:
                self._registry[inst_id] = DeploymentRecord(**data)
            except Exception:  # pragma: no cover - skip corrupt rows
                continue

    def _save_registry(self) -> None:
        os.makedirs(self._working_dir, exist_ok=True)
        serializable = {
            inst_id: json.loads(record.model_dump_json())
            for inst_id, record in self._registry.items()
        }
        with open(self._registry_path, "w") as f:
            json.dump(serializable, f)

    def get_deployment(self, instance_id: str) -> DeploymentRecord | None:
        return self._registry.get(instance_id)

    def list_deployments(self) -> list[DeploymentRecord]:
        return list(self._registry.values())

    def _inject_auth_env(self, config: ComputeConfig) -> dict[str, str | None]:
        original: dict[str, str | None] = {}
        if not config.provider_auth_aliases:
            return original
        for env_var, alias in config.provider_auth_aliases.items():
            original[env_var] = os.environ.get(env_var)
            if self._secrets_resolver:
                value = self._secrets_resolver.resolve(alias)
                if value is not None:
                    os.environ[env_var] = value
                    continue
            if alias in os.environ:
                os.environ[env_var] = os.environ[alias]
            else:
                raise RuntimeError(
                    f"Could not resolve auth alias {alias} for env var {env_var}. "
                    "Set the credential in OpenBao or as an environment variable."
                )
        return original

    def _restore_auth_env(self, original: dict[str, str | None]) -> None:
        for env_var, original_value in original.items():
            if original_value is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original_value

    async def deploy(self, config: ComputeConfig) -> ComputeInstance:
        self._last_config = config
        original_env = self._inject_auth_env(config)
        # Each deployment gets its OWN terraform working dir so its state is
        # isolated; destroy later runs in exactly this dir (deploy-before-destroy).
        deploy_dir = os.path.join(self._working_dir, f"d-{uuid.uuid4().hex[:12]}")
        os.makedirs(deploy_dir, exist_ok=True)
        self._active_working_dir = deploy_dir
        try:
            hcl = self._generator.generate(config)
            main_tf_path = os.path.join(deploy_dir, "main.tf")
            with open(main_tf_path, "w") as f:
                f.write(hcl)

            await self._run_terraform(["init", "-input=false"])
            await self._run_terraform(["apply", "-auto-approve", "-input=false"])

            output_result = await self._run_terraform(["output", "-json"])
            parsed = self._parse_outputs(output_result.get("stdout", ""))

            instance_id = parsed.get("instance_ip", parsed.get("pod_id", "unknown"))
            # W2.3 (C5/M2): record the deployment before returning. Now destroy can
            # look up its working dir and refuse instance_ids it never deployed.
            self._registry[instance_id] = DeploymentRecord(
                instance_id=instance_id,
                working_dir=deploy_dir,
                provider=config.provider.value,
                model_name=config.model_name,
                state="running",
                ip_address=parsed.get("instance_ip"),
                endpoint_url=parsed.get("endpoint_url"),
            )
            self._save_registry()
            return ComputeInstance(
                instance_id=instance_id,
                provider=config.provider,
                status="running",
                ip_address=parsed.get("instance_ip"),
                port=8000,
                gpu_type=config.gpu_type,
                endpoint_url=parsed.get("endpoint_url"),
            )
        finally:
            self._restore_auth_env(original_env)
            self._active_working_dir = self._working_dir

    async def destroy(self, instance_id: str) -> None:
        # W2.3 (C5): refuse to destroy an instance we have no record of. Running
        # terraform destroy blind was the money-leak — it could tear down the
        # wrong state, or none, while reporting success.
        record = self._registry.get(instance_id)
        if record is None:
            raise ValueError(
                f"Refusing to destroy unknown instance_id {instance_id!r}: "
                "no deployment record (deploy-before-destroy)."
            )
        original_env: dict[str, str | None] = {}
        if self._last_config is not None:
            original_env = self._inject_auth_env(self._last_config)
        self._active_working_dir = record.working_dir
        try:
            await self._run_terraform(["destroy", "-auto-approve", "-input=false"])
            self._registry.pop(instance_id, None)
            self._save_registry()
        finally:
            self._restore_auth_env(original_env)
            self._active_working_dir = self._working_dir

    async def _run_terraform(self, args: list[str]) -> dict[str, Any]:
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._active_working_dir,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"terraform failed (rc={proc.returncode}): {stderr.decode()}"
            )
        return {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
        }

    def _parse_outputs(self, output: str) -> dict[str, str]:
        if not output or not output.strip():
            return {}
        try:
            raw = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return {}
        result: dict[str, str] = {}
        for key, val in raw.items():
            if isinstance(val, dict) and "value" in val:
                result[key] = str(val["value"])
        return result
