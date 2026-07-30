"""Terraform/OpenTofu deployment lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.infra.azure_accelerator import effective_timeout_minutes
from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.schemas.deployment import DeploymentRecord

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "deployments.json"
_AZURE_CLIENT_SECRET_ENV = "_".join(("AZURE", "CLIENT", "SECRET"))
_ARM_CLIENT_SECRET_ENV = "_".join(("ARM", "CLIENT", "SECRET"))


class SecretsResolver(Protocol):
    def resolve(self, alias_name: str, project_id: str | None = None) -> str | None: ...


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

    def _build_auth_env(self, config: ComputeConfig) -> dict[str, str]:
        """Return a *copy* of os.environ with provider creds overlaid.

        Never mutates the global os.environ — each deploy/destroy call gets its
        own isolated mapping, preventing credential cross-contamination when
        multiple deployments run concurrently.
        """
        env = self._build_auth_env_from_aliases(config.provider_auth_aliases)
        if config.provider == ComputeProvider.AZURE:
            self._translate_azure_env(env)
        return env

    @staticmethod
    def _translate_azure_env(env: dict[str, str]) -> None:
        """Translate Azure SDK variables to azurerm names inside one env copy."""

        azure_to_arm = {
            "AZURE_SUBSCRIPTION_ID": "ARM_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID": "ARM_TENANT_ID",
            "AZURE_CLIENT_ID": "ARM_CLIENT_ID",
            _AZURE_CLIENT_SECRET_ENV: _ARM_CLIENT_SECRET_ENV,
        }
        for azure_name, arm_name in azure_to_arm.items():
            value = env.get(azure_name)
            if value and not env.get(arm_name):
                env[arm_name] = value
        if (
            env.get("ARM_CLIENT_ID")
            and not env.get(_ARM_CLIENT_SECRET_ENV)
            and not env.get("ARM_USE_MSI")
        ):
            env["ARM_USE_MSI"] = "true"

    def _build_auth_env_from_aliases(
        self,
        aliases: dict[str, str] | None,
    ) -> dict[str, str]:
        """Resolve stored alias names without persisting credential values."""

        env = os.environ.copy()
        if not aliases:
            return env
        for env_var, alias in aliases.items():
            if self._secrets_resolver:
                value = self._secrets_resolver.resolve(alias)
                if value is not None:
                    env[env_var] = value
                    continue
            if alias in os.environ:
                env[env_var] = os.environ[alias]
            else:
                raise RuntimeError(
                    f"Could not resolve auth alias {alias} for env var {env_var}. "
                    "Set the credential in OpenBao or as an environment variable."
                )
        return env

    async def deploy(self, config: ComputeConfig) -> ComputeInstance:
        self._last_config = config
        # Build a per-call env snapshot; global os.environ is never mutated.
        auth_env = self._build_auth_env(config)
        # Each deployment gets its OWN terraform working dir so its state is
        # isolated; destroy later runs in exactly this dir (deploy-before-destroy).
        deployment_token = uuid.uuid4().hex[:12]
        deploy_dir = os.path.join(self._working_dir, f"d-{deployment_token}")
        os.makedirs(deploy_dir, exist_ok=True)
        terraform_dir = deploy_dir
        initialized = False
        try:
            if config.provider == ComputeProvider.AZURE and config.deploy_type == "vm":
                terraform_dir = str(
                    self._generator.materialize(
                        config,
                        deploy_dir,
                        deployment_name=f"gludd-{deployment_token}",
                    )
                )
            else:
                hcl = self._generator.generate(config)
                main_tf_path = os.path.join(deploy_dir, "main.tf")
                with open(main_tf_path, "w") as f:
                    f.write(hcl)

            await self._run_terraform(
                ["init", "-input=false"],
                cwd=terraform_dir,
                env=auth_env,
            )
            initialized = True
            await self._run_terraform(
                ["apply", "-auto-approve", "-input=false"],
                cwd=terraform_dir,
                env=auth_env,
            )

            output_result = await self._run_terraform(
                ["output", "-json"],
                cwd=terraform_dir,
                env=auth_env,
            )
            parsed = self._parse_outputs(output_result.get("stdout", ""))
            if not parsed:
                parsed = {
                    key: str(value)
                    for key, value in output_result.items()
                    if key in {
                        "deployment_id",
                        "instance_id",
                        "instance_ip",
                        "pod_id",
                        "endpoint_url",
                        "base_url",
                    }
                    and value
                }

            instance_id = (
                parsed.get("deployment_id")
                or parsed.get("instance_id")
                or parsed.get("pod_id")
                or parsed.get("instance_ip")
            )
            if not instance_id:
                raise RuntimeError(
                    "terraform apply returned no deployment identifier; "
                    "rollback was requested"
                )
            endpoint_url = parsed.get("endpoint_url") or parsed.get("base_url")
            created_at = datetime.now(UTC)
            timeout_minutes = effective_timeout_minutes(
                requested_timeout_minutes=config.timeout_minutes,
                max_cost_usd=config.max_cost_usd,
                hourly_rate_usd=config.hourly_rate_usd,
            )
            # W2.3 (C5/M2): record the deployment before returning. Now destroy can
            # look up its working dir and refuse instance_ids it never deployed.
            self._registry[instance_id] = DeploymentRecord(
                instance_id=instance_id,
                working_dir=terraform_dir,
                provider=config.provider.value,
                model_name=config.model_name,
                state="running",
                ip_address=parsed.get("instance_ip"),
                endpoint_url=endpoint_url,
                created_at=created_at,
                expires_at=created_at + timedelta(minutes=timeout_minutes),
                provider_auth_aliases=config.provider_auth_aliases,
            )
            self._save_registry()
            return ComputeInstance(
                instance_id=instance_id,
                provider=config.provider,
                status="running",
                ip_address=parsed.get("instance_ip"),
                port=8000,
                gpu_type=config.gpu_type,
                endpoint_url=endpoint_url,
            )
        except Exception:
            # Terraform apply can leave partial paid resources even when it
            # exits non-zero (notably Azure allocation/extension failures).
            # Once init succeeded, always request a best-effort destroy before
            # surfacing the original failure.
            if initialized:
                try:
                    await self._run_terraform(
                        ["destroy", "-auto-approve", "-input=false"],
                        cwd=terraform_dir,
                        env=auth_env,
                    )
                except Exception:
                    logger.exception(
                        "automatic rollback failed for terraform dir %s",
                        terraform_dir,
                    )
            raise

    async def plan(self, config: ComputeConfig) -> dict[str, Any]:
        auth_env = self._build_auth_env(config)
        plan_token = uuid.uuid4().hex[:12]
        plan_dir = os.path.join(self._working_dir, f"p-{plan_token}")
        os.makedirs(plan_dir, exist_ok=True)
        terraform_dir = plan_dir
        if config.provider == ComputeProvider.AZURE and config.deploy_type == "vm":
            terraform_dir = str(
                self._generator.materialize(
                    config,
                    plan_dir,
                    deployment_name=f"gludd-plan-{plan_token}",
                )
            )
        else:
            hcl = self._generator.generate(config)
            with open(os.path.join(plan_dir, "main.tf"), "w") as f:
                f.write(hcl)
        await self._run_terraform(
            ["init", "-input=false"],
            cwd=terraform_dir,
            env=auth_env,
        )
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary, "plan", "-detailed-exitcode", "-input=false",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=terraform_dir,
            env=auth_env,
        )
        stdout, stderr = await proc.communicate()
        changes = proc.returncode == 2
        if proc.returncode not in (0, 2):
            raise RuntimeError(
                f"terraform plan failed (rc={proc.returncode}): {stderr.decode()}"
            )
        return {
            "changes_present": changes,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
        }

    async def validate(self, config: ComputeConfig) -> dict[str, Any]:
        auth_env = self._build_auth_env(config)
        validate_token = uuid.uuid4().hex[:12]
        val_dir = os.path.join(self._working_dir, f"v-{validate_token}")
        os.makedirs(val_dir, exist_ok=True)
        terraform_dir = val_dir
        if config.provider == ComputeProvider.AZURE and config.deploy_type == "vm":
            terraform_dir = str(
                self._generator.materialize(
                    config,
                    val_dir,
                    deployment_name=f"gludd-validate-{validate_token}",
                )
            )
        else:
            hcl = self._generator.generate(config)
            with open(os.path.join(val_dir, "main.tf"), "w") as f:
                f.write(hcl)
        await self._run_terraform(
            ["init", "-input=false"],
            cwd=terraform_dir,
            env=auth_env,
        )
        return await self._run_terraform(
            ["validate", "-json"],
            cwd=terraform_dir,
            env=auth_env,
        )

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
        # Build a per-call env snapshot; global os.environ is never mutated.
        auth_env = self._build_auth_env_from_aliases(
            record.provider_auth_aliases
        )
        if record.provider == ComputeProvider.AZURE.value:
            self._translate_azure_env(auth_env)
        try:
            await self._run_terraform(
                ["destroy", "-auto-approve", "-input=false"],
                cwd=record.working_dir,
                env=auth_env,
            )
            self._registry.pop(instance_id, None)
            self._save_registry()
        finally:
            pass  # no env cleanup needed — we never touched os.environ

    async def _destroy_at_expiry(self, instance_id: str) -> bool:
        """Destroy one expired deployment, returning whether cleanup ran."""

        record = self._registry.get(instance_id)
        if record is None or record.expires_at is None:
            return False
        if record.expires_at > datetime.now(UTC):
            return False
        await self.destroy(instance_id)
        return True

    async def cleanup_expired(self) -> list[str]:
        """Destroy every persisted deployment whose hard TTL has elapsed.

        The daemon calls this from its recurring compute-utilization phase, so
        records loaded after a restart are cleaned without relying on an
        in-memory timer that disappears with the process.
        """

        destroyed: list[str] = []
        for instance_id in tuple(self._registry):
            try:
                if await self._destroy_at_expiry(instance_id):
                    destroyed.append(instance_id)
            except Exception:
                record = self._registry.get(instance_id)
                if record is not None:
                    record.state = "cleanup_retry"
                    self._save_registry()
                logger.exception(
                    "expired deployment cleanup failed for %s; retrying next tick",
                    instance_id,
                )
        return destroyed

    async def _run_terraform(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd if cwd is not None else self._active_working_dir,
            env=env,
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
