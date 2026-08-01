"""Terraform/OpenTofu deployment lifecycle manager."""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.infra.compute import ComputeConfig, ComputeInstance
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.schemas.deployment import DeploymentRecord

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "deployments.json"

GPU_PRIORITY_REGIONS = [
    "westus2",
    "northeurope",
    "westeurope",
    "eastus",
    "centralus",
    "eastus2",
    "southcentralus",
]

_DEPLOYED_INSTANCES: dict[str, str] = {}  # instance_id → deploy_dir


def _get_all_regions() -> list[str]:
    try:
        result = subprocess.run(
            ["az", "account", "list-locations", "--query", "[?regionalDisplayName].name", "-o", "tsv"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [r.strip() for r in result.stdout.splitlines() if r.strip()]
    except Exception:
        pass
    return []


def _discover_azure_regions() -> list[str]:
    cache_path = "/tmp/gludd-azure-regions.json"

    try:
        if os.path.exists(cache_path):
            data = json.loads(Path(cache_path).read_text())
            if time.time() - data.get("ts", 0) < 86400:
                return data["regions"]
    except Exception:
        pass

    all_regions = _get_all_regions()
    ordered = [r for r in GPU_PRIORITY_REGIONS if r in all_regions]
    ordered += [r for r in all_regions if r not in ordered]

    try:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps({"ts": time.time(), "regions": ordered}))
    except Exception:
        pass

    return ordered or GPU_PRIORITY_REGIONS


async def _destroy_instance(instance_id: str, deploy_dir: str) -> None:
    resolver = BinaryPathResolver()
    binary = resolver.get_infra_binary()
    proc = await asyncio.create_subprocess_exec(
        binary,
        "destroy",
        "-auto-approve",
        "-input=false",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=deploy_dir,
    )
    await proc.communicate()


def _cleanup_orphaned_instances() -> None:
    for instance_id, deploy_dir in list(_DEPLOYED_INSTANCES.items()):
        try:
            logger.warning("Cleaning up orphaned instance %s", instance_id)
            asyncio.run(_destroy_instance(instance_id, deploy_dir))
        except Exception:
            logger.exception("Failed to clean up orphaned instance %s", instance_id)


atexit.register(_cleanup_orphaned_instances)


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
        serializable = {inst_id: json.loads(record.model_dump_json()) for inst_id, record in self._registry.items()}
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
        env = os.environ.copy()
        if not config.provider_auth_aliases:
            return env
        for env_var, alias in config.provider_auth_aliases.items():
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
        auth_env = self._build_auth_env(config)
        deploy_dir = os.path.join(self._working_dir, f"d-{uuid.uuid4().hex[:12]}")
        os.makedirs(deploy_dir, exist_ok=True)

        regions = [config.region] if config.region else _discover_azure_regions()
        last_error = None

        for region in regions:
            print(f"[deploy] Trying region {region}...", flush=True)
            config.region = region
            hcl = self._generator.generate(config)
            main_tf_path = os.path.join(deploy_dir, "main.tf")
            with open(main_tf_path, "w") as f:
                f.write(hcl)

            try:
                print(f"[deploy] Terraform init in {region}...", flush=True)
                await self._run_terraform(["init", "-input=false"], cwd=deploy_dir, env=auth_env)
                print("[deploy] Terraform init done", flush=True)
                print(f"[deploy] Terraform apply in {region} (this takes 3-5min)...", flush=True)
                await self._run_terraform(["apply", "-auto-approve", "-input=false"], cwd=deploy_dir, env=auth_env)
                print(f"[deploy] Terraform apply done in {region}", flush=True)
                break  # Success — exit the region loop
            except RuntimeError as e:
                last_error = e
                error_str = str(e)
                if "AKSCapacityHeavyUsage" in error_str or "capacity" in error_str.lower():
                    print(f"[deploy] Region {region} at capacity, trying next...", flush=True)
                    continue  # Try next region
                raise  # Not a capacity error — re-raise

        if last_error:
            raise last_error

        output_result = await self._run_terraform(["output", "-json"], cwd=deploy_dir, env=auth_env)
        parsed = self._parse_outputs(output_result.get("stdout", ""))

        instance_id = parsed.get("instance_ip", parsed.get("pod_id", "unknown"))
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
        _DEPLOYED_INSTANCES[instance_id] = deploy_dir
        return ComputeInstance(
            instance_id=instance_id,
            provider=config.provider,
            status="running",
            ip_address=parsed.get("instance_ip"),
            port=8000,
            gpu_type=config.gpu_type,
            endpoint_url=parsed.get("endpoint_url"),
        )

    async def plan(self, config: ComputeConfig) -> dict[str, Any]:
        auth_env = self._build_auth_env(config)
        plan_dir = os.path.join(self._working_dir, f"p-{uuid.uuid4().hex[:12]}")
        os.makedirs(plan_dir, exist_ok=True)
        hcl = self._generator.generate(config)
        with open(os.path.join(plan_dir, "main.tf"), "w") as f:
            f.write(hcl)
        await self._run_terraform(["init", "-input=false"], cwd=plan_dir, env=auth_env)
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            "plan",
            "-detailed-exitcode",
            "-input=false",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=plan_dir,
            env=auth_env,
        )
        stdout, stderr = await proc.communicate()
        changes = proc.returncode == 2
        if proc.returncode not in (0, 2):
            raise RuntimeError(f"terraform plan failed (rc={proc.returncode}): {stderr.decode()}")
        return {
            "changes_present": changes,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
        }

    async def validate(self, config: ComputeConfig) -> dict[str, Any]:
        auth_env = self._build_auth_env(config)
        val_dir = os.path.join(self._working_dir, f"v-{uuid.uuid4().hex[:12]}")
        os.makedirs(val_dir, exist_ok=True)
        hcl = self._generator.generate(config)
        with open(os.path.join(val_dir, "main.tf"), "w") as f:
            f.write(hcl)
        await self._run_terraform(["init", "-input=false"], cwd=val_dir, env=auth_env)
        return await self._run_terraform(["validate", "-json"], cwd=val_dir, env=auth_env)

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
        auth_env: dict[str, str] | None = None
        if self._last_config is not None:
            auth_env = self._build_auth_env(self._last_config)
        try:
            await self._run_terraform(
                ["destroy", "-auto-approve", "-input=false"],
                cwd=record.working_dir,
                env=auth_env,
            )
            self._registry.pop(instance_id, None)
            _DEPLOYED_INSTANCES.pop(instance_id, None)
            self._save_registry()
        finally:
            pass  # no env cleanup needed — we never touched os.environ

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
            raise RuntimeError(f"terraform failed (rc={proc.returncode}): {stderr.decode()}")
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
