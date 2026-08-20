"""Terraform/OpenTofu deployment lifecycle manager."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.db.deployment_repository import DeploymentRegistryRepository
from general_ludd.events import CustomEvent
from general_ludd.infra.azure_accelerator import effective_timeout_minutes
from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider
from general_ludd.infra.deploy_strategy import ResourceTier
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.schemas.deployment import DeploymentRecord
from general_ludd.security.sanitize import sanitize_error_message
from general_ludd.security.state import project_state, secure_write_text

if TYPE_CHECKING:
    from general_ludd.cloud.resource_lifecycle import ResourceLifecycleManager

get_lifecycle: Callable[[], ResourceLifecycleManager] | None
try:
    from general_ludd.cloud.resource_lifecycle import get_lifecycle as _get_lifecycle

    get_lifecycle = _get_lifecycle
    _LIFECYCLE_IMPORTED = True
except ImportError:
    _LIFECYCLE_IMPORTED = False
    get_lifecycle = None

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
    cache_path = project_state().path("infra", "azure-regions.json")

    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            cached_regions = data.get("regions")
            if (
                time.time() - data.get("ts", 0) < 86400
                and isinstance(cached_regions, list)
                and all(isinstance(region, str) for region in cached_regions)
            ):
                return [str(region) for region in cached_regions]
    except Exception:
        pass

    all_regions = _get_all_regions()
    ordered = [r for r in GPU_PRIORITY_REGIONS if r in all_regions]
    ordered += [r for r in all_regions if r not in ordered]

    with contextlib.suppress(Exception):
        secure_write_text(
            cache_path,
            json.dumps({"ts": time.time(), "regions": ordered}),
        )

    return ordered or GPU_PRIORITY_REGIONS


def _destroy_instance(instance_id: str, deploy_dir: str) -> None:
    """Destroy from signal/atexit paths without starting a nested event loop."""
    if not os.path.isdir(deploy_dir) or not (
        os.path.isfile(os.path.join(deploy_dir, "terraform.tfstate"))
        or os.path.isdir(os.path.join(deploy_dir, ".terraform"))
    ):
        logger.info(
            "Skipping orphan cleanup for %s: no initialized terraform state in %s",
            instance_id,
            deploy_dir,
        )
        _DEPLOYED_INSTANCES.pop(instance_id, None)
        return
    resolver = BinaryPathResolver()
    binary = resolver.get_infra_binary()
    result = subprocess.run(
        [binary, "destroy", "-auto-approve", "-input=false"],
        cwd=deploy_dir,
        check=False,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"terraform destroy failed for {instance_id!r} with rc={result.returncode}")
    _DEPLOYED_INSTANCES.pop(instance_id, None)


def _cleanup_orphaned_instances() -> None:
    if _LIFECYCLE_IMPORTED and get_lifecycle is not None:
        cleaned = get_lifecycle().cleanup_all()
        if cleaned:
            logger.warning("Lifecycle cleanup: %d resources destroyed", cleaned)
        else:
            logger.info("Lifecycle cleanup: no tracked resources")
    for instance_id, deploy_dir in list(_DEPLOYED_INSTANCES.items()):
        try:
            logger.warning("Cleaning up orphaned instance %s", instance_id)
            _destroy_instance(instance_id, deploy_dir)
        except Exception:
            logger.exception("Failed to clean up orphaned instance %s", instance_id)


atexit.register(_cleanup_orphaned_instances)


def _handle_signal(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT by cleaning up deployed instances, then re-raise."""
    logger.warning("Received signal %d — cleaning up deployed instances", signum)
    _cleanup_orphaned_instances()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


_signal_handlers_installed = False


def _install_signal_handlers() -> bool:
    """Install deployment cleanup handlers when Python permits it.

    Imports can happen inside ``asyncio.to_thread`` on the worker generation
    path.  Python only permits ``signal.signal`` in the main thread of the main
    interpreter, so a worker-thread import must remain process-pure and let a
    later main-thread manager construction retry installation.  The ValueError
    guard is still required for embedded interpreters where
    ``threading.main_thread`` can differ from the interpreter's signal thread.
    """
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return True
    if threading.current_thread() is not threading.main_thread():
        logger.debug("Skipping deployment signal handlers outside the main thread")
        return False
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except ValueError:
        logger.debug(
            "Skipping deployment signal handlers outside the main interpreter",
            exc_info=True,
        )
        return False
    _signal_handlers_installed = True
    return True


_install_signal_handlers()


class SecretsResolver(Protocol):
    """Represent ``SecretsResolver`` values."""

    def resolve(self, alias_name: str, project_id: str | None = None) -> str | None:
        """Resolve a secret alias for an optional project."""
        ...


class EventPublisher(Protocol):
    """Represent ``EventPublisher`` values."""

    def publish(self, event: Any) -> int:
        """Publish an event and return the subscriber count."""
        ...


class DeploymentManager:
    """Represent ``DeploymentManager`` values."""
    def __init__(
        self,
        binary_paths: BinaryPathResolver | None = None,
        working_dir: str | None = None,
        secrets_resolver: SecretsResolver | None = None,
        event_bus: EventPublisher | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        worker_id: str | None = None,
    ) -> None:
        """Initialize a ``DeploymentManager`` instance."""
        _install_signal_handlers()
        self._binary_resolver = binary_paths or BinaryPathResolver()
        self._working_dir = working_dir or str(project_state().temporary_directory("terraform", prefix="gludd-tf-"))
        # The dir the NEXT terraform invocation runs in. deploy()/destroy() point
        # this at the per-instance dir so one manager can hold many deployments.
        self._active_working_dir = self._working_dir
        self._generator = TerraformGenerator()
        self._secrets_resolver = secrets_resolver
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._worker_id = (worker_id or f"deployment-{os.getpid()}")[:128]
        self._last_config: ComputeConfig | None = None
        # W2.3 (C5/M2): instance_id -> DeploymentRecord, persisted to disk so a
        # restart still knows what is deployed and where (deploy-before-destroy).
        self._registry: dict[str, DeploymentRecord] = {}
        if self._session_factory is None:
            self._load_registry()

    def _publish_event(self, name: str, **payload: Any) -> None:
        """Publish deployment progress without letting observers break lifecycle work."""
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(
                CustomEvent(
                    name=name,
                    payload=payload,
                    source="terraform_deployment",
                )
            )
        except Exception:
            logger.exception("Deployment event subscriber failed for %s", name)

    def close(self) -> None:
        """Close the value."""
        if self._working_dir and os.path.isdir(self._working_dir):
            import shutil

            shutil.rmtree(self._working_dir, ignore_errors=True)
            self._working_dir = ""

    @staticmethod
    def _estimate_elapsed_cost(config: ComputeConfig, elapsed_seconds: float) -> float:
        """Attribute elapsed Azure runtime using the strategist's tier rates."""
        if config.provider != ComputeProvider.AZURE:
            return 0.0
        if config.deploy_type == "containerapp":
            tier = ResourceTier.CONTAINER_APP
        elif config.spot or config.deploy_type == "vm_spot":
            tier = ResourceTier.SPOT_VM
        else:
            tier = ResourceTier.DEDICATED_VM
        gpu_multiplier = config.gpu_count if tier is not ResourceTier.CONTAINER_APP else 1
        return tier.cost_per_hour * max(elapsed_seconds, 0.0) * gpu_multiplier / 3600.0

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
        """Return get deployment."""
        return self._registry.get(instance_id)

    def list_deployments(self) -> list[DeploymentRecord]:
        """List deployments."""
        return list(self._registry.values())

    async def get_deployment_shared(self, instance_id: str) -> DeploymentRecord | None:
        """Read the shared registry when configured, keeping the local cache coherent."""
        if self._session_factory is None:
            return self.get_deployment(instance_id)
        async with self._session_factory() as session:
            record = await DeploymentRegistryRepository(session).get(instance_id)
        if record is None:
            self._registry.pop(instance_id, None)
        else:
            self._registry[instance_id] = record
        return record

    async def list_deployments_shared(self) -> list[DeploymentRecord]:
        """List all deployments from the cross-worker source of truth."""
        if self._session_factory is None:
            return self.list_deployments()
        async with self._session_factory() as session:
            records = await DeploymentRegistryRepository(session).list()
        self._registry = {record.instance_id: record for record in records}
        return records

    async def _persist_record(self, record: DeploymentRecord) -> None:
        self._registry[record.instance_id] = record
        if self._session_factory is None:
            self._save_registry()
            return
        async with self._session_factory() as session:
            await DeploymentRegistryRepository(session).upsert(record)
            await session.commit()

    def cleanup_orphaned_instances(self) -> int:
        """Leave process-global lifecycle cleanup to the registered shutdown hook."""
        return 0

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
        """Translate Azure SDK variables to azurerm names in one env copy."""
        azure_to_arm = {
            "AZURE_SUBSCRIPTION_ID": "ARM_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID": "ARM_TENANT_ID",
            "AZURE_CLIENT_ID": "ARM_CLIENT_ID",
            "AZURE_CLIENT_SECRET": "ARM_CLIENT_SECRET",
        }
        for azure_name, arm_name in azure_to_arm.items():
            value = env.get(azure_name)
            if value and not env.get(arm_name):
                env[arm_name] = value
        if (
            env.get("ARM_CLIENT_ID")
            and not env.get("ARM_CLIENT_SECRET")
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

    def _prepare_terraform_dir(
        self,
        config: ComputeConfig,
        root_dir: str,
        *,
        deployment_name: str,
    ) -> str:
        """Materialize Azure release assets or generate a provider root."""
        if config.provider == ComputeProvider.AZURE:
            return str(
                self._generator.materialize(
                    config,
                    root_dir,
                    deployment_name=deployment_name,
                )
            )
        main_tf_path = os.path.join(root_dir, "main.tf")
        with open(main_tf_path, "w") as stream:
            stream.write(self._generator.generate(config))
        return root_dir

    async def _rollback_terraform(
        self,
        terraform_dir: str,
        auth_env: dict[str, str],
    ) -> None:
        """Request best-effort teardown of a partially applied state."""
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

    async def deploy(self, config: ComputeConfig) -> ComputeInstance:
        """Deploy the value."""
        started_at = time.monotonic()
        self._last_config = config
        auth_env = self._build_auth_env(config)
        deployment_token = uuid.uuid4().hex[:12]
        deployment_id = f"d-{deployment_token}"
        deploy_dir = os.path.join(self._working_dir, deployment_id)
        os.makedirs(deploy_dir, exist_ok=True)
        terraform_dir = deploy_dir
        initialized = False
        _DEPLOYED_INSTANCES[deployment_id] = deploy_dir
        self._publish_event(
            "terraform_deploy_started",
            deployment_id=deployment_id,
            provider=config.provider.value,
            gpu_type=config.gpu_type.value,
            deploy_type=config.deploy_type,
            region=config.region,
        )

        if config.region:
            regions: list[str | None] = [config.region]
        elif config.provider == ComputeProvider.AZURE:
            regions = list(_discover_azure_regions())
        else:
            regions = [None]
        last_error: RuntimeError | None = None

        try:
            for region in regions:
                label = region or "default"
                print(f"[deploy] Trying region {label}...", flush=True)
                config.region = region
                terraform_dir = self._prepare_terraform_dir(
                    config,
                    deploy_dir,
                    deployment_name=f"gludd-{deployment_token}",
                )
                _DEPLOYED_INSTANCES[deployment_id] = terraform_dir
                initialized = False

                try:
                    print(f"[deploy] Terraform init in {label}...", flush=True)
                    await self._run_terraform(
                        ["init", "-input=false"],
                        cwd=terraform_dir,
                        env=auth_env,
                    )
                    initialized = True
                    print("[deploy] Terraform init done", flush=True)
                    print(
                        f"[deploy] Terraform apply in {label} "
                        "(this takes 3-5min)...",
                        flush=True,
                    )
                    await self._run_terraform(
                        ["apply", "-auto-approve", "-input=false"],
                        cwd=terraform_dir,
                        env=auth_env,
                    )
                    print(
                        f"[deploy] Terraform apply done in {label}",
                        flush=True,
                    )
                    last_error = None
                    break
                except RuntimeError as error:
                    last_error = error
                    if initialized:
                        await self._rollback_terraform(terraform_dir, auth_env)
                        initialized = False
                    error_str = str(error)
                    if (
                        "AKSCapacityHeavyUsage" in error_str
                        or "capacity" in error_str.lower()
                    ):
                        print(
                            f"[deploy] Region {region} at capacity, trying next...",
                            flush=True,
                        )
                        continue
                    raise

            if last_error:
                raise last_error

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
                    if key
                    in {
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
            elapsed_seconds = time.monotonic() - started_at
            cost_incurred = self._estimate_elapsed_cost(config, elapsed_seconds)
            created_at = datetime.now(UTC)
            timeout_minutes = effective_timeout_minutes(
                requested_timeout_minutes=config.timeout_minutes,
                max_cost_usd=config.max_cost_usd,
                hourly_rate_usd=config.hourly_rate_usd,
            )
            record = DeploymentRecord(
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
            await self._persist_record(record)
            _DEPLOYED_INSTANCES.pop(deployment_id, None)
            _DEPLOYED_INSTANCES[instance_id] = terraform_dir

            if _LIFECYCLE_IMPORTED and get_lifecycle is not None:
                get_lifecycle().register(
                    config.provider.value,
                    instance_id,
                    terraform_dir,
                )

            instance = ComputeInstance(
                instance_id=instance_id,
                provider=config.provider,
                status="running",
                ip_address=parsed.get("instance_ip"),
                port=8000,
                gpu_type=config.gpu_type,
                endpoint_url=endpoint_url,
                cost_incurred=cost_incurred,
            )
            self._publish_event(
                "terraform_deploy_completed",
                deployment_id=deployment_id,
                instance_id=instance_id,
                provider=config.provider.value,
                region=config.region,
                elapsed_seconds=elapsed_seconds,
                cost_incurred_usd=cost_incurred,
            )
            return instance
        except BaseException as error:
            if initialized:
                await self._rollback_terraform(terraform_dir, auth_env)
            self._publish_event(
                "terraform_deploy_failed",
                deployment_id=deployment_id,
                provider=config.provider.value,
                region=config.region,
                error=sanitize_error_message(str(error)),
            )
            raise

    async def plan(self, config: ComputeConfig) -> dict[str, Any]:
        """Plan the value."""
        auth_env = self._build_auth_env(config)
        plan_token = uuid.uuid4().hex[:12]
        plan_dir = os.path.join(self._working_dir, f"p-{plan_token}")
        os.makedirs(plan_dir, exist_ok=True)
        terraform_dir = self._prepare_terraform_dir(
            config,
            plan_dir,
            deployment_name=f"gludd-plan-{plan_token}",
        )
        await self._run_terraform(
            ["init", "-input=false"],
            cwd=terraform_dir,
            env=auth_env,
        )
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            "plan",
            "-detailed-exitcode",
            "-input=false",
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
        """Validate the value."""
        auth_env = self._build_auth_env(config)
        validation_token = uuid.uuid4().hex[:12]
        val_dir = os.path.join(self._working_dir, f"v-{validation_token}")
        os.makedirs(val_dir, exist_ok=True)
        terraform_dir = self._prepare_terraform_dir(
            config,
            val_dir,
            deployment_name=f"gludd-validate-{validation_token}",
        )
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
        """Destroy the value."""
        record = await self.get_deployment_shared(instance_id)
        if record is None:
            raise ValueError(
                f"Refusing to destroy unknown instance_id {instance_id!r}: "
                "no deployment record (deploy-before-destroy)."
            )
        # Resolve stored alias names into one per-call environment snapshot.
        auth_env = self._build_auth_env_from_aliases(
            record.provider_auth_aliases
        )
        if record.provider == ComputeProvider.AZURE.value:
            self._translate_azure_env(auth_env)
        database_claimed = False
        if self._session_factory is not None:
            async with self._session_factory() as session:
                try:
                    record = await DeploymentRegistryRepository(session).claim_for_destroy(
                        instance_id,
                        owner=self._worker_id,
                    )
                except KeyError:
                    raise ValueError(
                        f"Refusing to destroy unknown instance_id {instance_id!r}: "
                        "no deployment record (deploy-before-destroy)."
                    ) from None
                await session.commit()
                database_claimed = True
            self._registry[instance_id] = record
        self._publish_event(
            "terraform_destroy_started",
            instance_id=instance_id,
            provider=record.provider,
        )
        cancelled = False
        try:
            destroy_task = asyncio.create_task(
                self._run_terraform(
                    ["destroy", "-auto-approve", "-input=false"],
                    cwd=record.working_dir,
                    env=auth_env,
                )
            )
            try:
                await asyncio.shield(destroy_task)
            except asyncio.CancelledError:
                # A request/test timeout must not cancel the chargeable resource
                # cleanup. Finish terraform destroy, then preserve cancellation
                # semantics for the caller.
                cancelled = True
                await destroy_task
            if self._session_factory is not None:
                async with self._session_factory() as session:
                    await DeploymentRegistryRepository(session).finish_destroy(
                        instance_id,
                        owner=self._worker_id,
                    )
                    await session.commit()
                database_claimed = False
            self._registry.pop(instance_id, None)
            _DEPLOYED_INSTANCES.pop(instance_id, None)
            if self._session_factory is None:
                self._save_registry()

            if _LIFECYCLE_IMPORTED and get_lifecycle is not None:
                get_lifecycle().deregister(instance_id)
            self._publish_event(
                "terraform_destroy_completed",
                instance_id=instance_id,
                provider=record.provider,
            )
            if cancelled:
                raise asyncio.CancelledError
        except BaseException as error:
            if database_claimed and self._session_factory is not None:
                try:
                    async with self._session_factory() as session:
                        await DeploymentRegistryRepository(session).release_destroy(
                            instance_id,
                            owner=self._worker_id,
                        )
                        await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to release destroy claim for %s owned by %s",
                        instance_id,
                        self._worker_id,
                    )
            if not (cancelled and isinstance(error, asyncio.CancelledError)):
                self._publish_event(
                    "terraform_destroy_failed",
                    instance_id=instance_id,
                    provider=record.provider,
                    error=sanitize_error_message(str(error)),
                )
            raise

    async def _destroy_at_expiry(self, instance_id: str) -> bool:
        """Destroy one expired deployment, returning whether cleanup ran."""
        record = await self.get_deployment_shared(instance_id)
        if record is None or record.expires_at is None:
            return False
        if record.expires_at > datetime.now(UTC):
            return False
        await self.destroy(instance_id)
        return True

    async def cleanup_expired(self) -> list[str]:
        """Destroy all persisted deployments whose hard TTL has elapsed."""
        records = await self.list_deployments_shared()
        destroyed: list[str] = []
        for record in records:
            try:
                if await self._destroy_at_expiry(record.instance_id):
                    destroyed.append(record.instance_id)
            except Exception:
                record.state = "cleanup_retry"
                await self._persist_record(record)
                logger.exception(
                    "expired deployment cleanup failed for %s; "
                    "retrying next tick",
                    record.instance_id,
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
        operation = args[0] if args else "unknown"
        working_dir = cwd if cwd is not None else self._active_working_dir
        self._publish_event(
            "terraform_command_started",
            operation=operation,
            working_dir=os.path.basename(working_dir),
        )
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd if cwd is not None else self._active_working_dir,
            env=env,
        )
        lines: list[str] = []
        assert proc.stdout is not None

        def record_output(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()
            lines.append(text)
            message = sanitize_error_message(text.rstrip())[-2000:]
            if message:
                self._publish_event(
                    "terraform_output",
                    operation=operation,
                    message=message,
                )

        if isinstance(proc.stdout, (bytes, bytearray)):
            record_output(bytes(proc.stdout).decode(errors="replace"))
        else:
            while True:
                chunk = await proc.stdout.readline()
                if not chunk:
                    break
                record_output(chunk.decode(errors="replace"))
        await proc.wait()
        output = "".join(lines)
        if proc.returncode != 0:
            self._publish_event(
                "terraform_command_failed",
                operation=operation,
                returncode=proc.returncode,
                error=sanitize_error_message(output[-2000:]),
            )
            raise RuntimeError(f"terraform failed (rc={proc.returncode}): {output[-2000:]}")
        self._publish_event(
            "terraform_command_completed",
            operation=operation,
            returncode=proc.returncode,
        )
        return {
            "stdout": output,
            "stderr": "",
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
