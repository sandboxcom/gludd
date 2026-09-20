"""Remote Ansible adapter for provider-neutral model-worker lifecycle roles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import yaml

from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ModelWorkerLifecyclePolicy,
    ProvisionedModelWorkerPool,
)

_SERVICE_NAME = re.compile(r"^gludd-model-worker-[0-9a-f]{12}[.]service$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANDIDATE_FIELDS = frozenset(
    {
        "adapter_id",
        "attestation_path",
        "backend",
        "devices_per_replica",
        "driver_version_digest",
        "health_url",
        "interconnect",
        "observed_inventory_digest",
        "ready",
        "release_id",
        "runner_id",
        "runtime_version_digest",
        "service_name",
        "source_revision",
        "topology_digest",
    }
)


class _PlaybookRunner(Protocol):
    """Minimal hardened Ansible adapter surface used by this runtime."""

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
        """Run one registered playbook and return its normalized result."""
        ...


class ModelWorkerConfigurationError(RuntimeError):
    """Censored configuration failure safe for lifecycle telemetry."""

    def __init__(self, phase: str) -> None:
        """Retain one stable phase without provider, host, or credential detail."""
        self.phase = phase
        super().__init__(f"model worker configuration failed during {phase}")


class AnsibleModelWorkerConfigurationRuntime:
    """Attest, launch, health-check, and retire remote model workers."""

    def __init__(
        self,
        runner: _PlaybookRunner,
        *,
        retire_timeout_seconds: int = 300,
    ) -> None:
        """Bind one runner and a bounded retirement deadline."""
        if not callable(getattr(runner, "run_playbook", None)):
            raise ValueError("runner must expose run_playbook")
        if (
            isinstance(retire_timeout_seconds, bool)
            or not isinstance(retire_timeout_seconds, int)
            or not 1 <= retire_timeout_seconds <= 3_600
        ):
            raise ValueError("retire_timeout_seconds is outside 1..3600")
        self._runner = runner
        self._retire_timeout_seconds = retire_timeout_seconds

    @staticmethod
    def _inventory_document(
        deployment: ProvisionedModelWorkerPool,
    ) -> tuple[dict[str, object], dict[str, str]]:
        aliases: dict[str, str] = {}
        hosts: dict[str, dict[str, str]] = {}
        for index, host in enumerate(deployment.hosts):
            alias = f"gludd_worker_{index:04d}"
            aliases[alias] = host.host_id
            hosts[alias] = {
                "ansible_host": host.address,
                "ansible_ssh_private_key_file": host.ssh_private_key_path,
                "ansible_user": host.ansible_user,
            }
        document: dict[str, object] = {
            "all": {
                "children": {
                    "gludd_model_workers": {
                        "hosts": hosts,
                    }
                }
            }
        }
        return document, aliases

    @contextmanager
    def _inventory(
        self,
        deployment: ProvisionedModelWorkerPool,
    ) -> Iterator[tuple[Path, dict[str, str]]]:
        document, aliases = self._inventory_document(deployment)
        with tempfile.TemporaryDirectory(prefix="gludd-model-worker-inventory-") as root:
            path = Path(root) / "inventory.yml"
            path.write_text(
                yaml.safe_dump(document, default_flow_style=False, sort_keys=True),
                encoding="utf-8",
            )
            os.chmod(path, 0o600)
            yield path, aliases

    def _run(
        self,
        *,
        playbook_name: str,
        deployment: ProvisionedModelWorkerPool,
        extravars: dict[str, object],
        timeout_seconds: int,
    ) -> tuple[Mapping[str, object], dict[str, str]]:
        with self._inventory(deployment) as (inventory_path, aliases):
            raw = self._runner.run_playbook(
                playbook_name=playbook_name,
                inventory=[str(inventory_path)],
                extravars=extravars,
                connection="ssh",
                become=True,
                timeout=timeout_seconds,
            )
        if not isinstance(raw, Mapping):
            raise ModelWorkerConfigurationError("runner_result")
        rc = raw.get("rc")
        if raw.get("status") != "successful" or isinstance(rc, bool) or rc != 0:
            raise ModelWorkerConfigurationError(
                "retire" if playbook_name == "model_worker_retire.yml" else "configure"
            )
        return raw, aliases

    @staticmethod
    def _candidate_facts(
        result: Mapping[str, object],
        aliases: Mapping[str, str],
    ) -> dict[str, Mapping[str, object]]:
        candidates: dict[str, Mapping[str, object]] = {}
        events = result.get("events")
        if not isinstance(events, list):
            return candidates
        for event in events:
            if not isinstance(event, Mapping) or event.get("event") != "runner_on_ok":
                continue
            alias = event.get("host")
            payload = event.get("result")
            if not isinstance(alias, str) or alias not in aliases:
                continue
            if not isinstance(payload, Mapping):
                continue
            facts = payload.get("ansible_facts")
            if not isinstance(facts, Mapping):
                continue
            candidate = facts.get("gludd_model_worker_candidate")
            if isinstance(candidate, Mapping):
                candidates[aliases[alias]] = candidate
        return candidates

    @staticmethod
    def _attestation_digest(candidate: Mapping[str, object]) -> str:
        encoded = json.dumps(
            dict(candidate),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _candidate_matches_policy(
        candidate: Mapping[str, object],
        policy: ModelWorkerLifecyclePolicy,
    ) -> bool:
        plan = policy.launch_plan
        service_name = candidate.get("service_name")
        return (
            frozenset(candidate) == _CANDIDATE_FIELDS
            and candidate.get("ready") is True
            and candidate.get("release_id") == policy.release_id
            and candidate.get("runner_id") == plan.runner_id
            and candidate.get("adapter_id") == plan.adapter_id
            and candidate.get("source_revision") == plan.source_revision
            and candidate.get("topology_digest") == policy.topology_digest
            and candidate.get("runtime_version_digest")
            == policy.expected_runtime_version_digest
            and candidate.get("backend") == policy.backend
            and candidate.get("interconnect") == policy.required_interconnect
            and candidate.get("devices_per_replica") == plan.devices_per_replica
            and not isinstance(candidate.get("devices_per_replica"), bool)
            and isinstance(service_name, str)
            and service_name
            == f"gludd-model-worker-{policy.release_id[:12]}.service"
            and _SERVICE_NAME.fullmatch(service_name) is not None
            and isinstance(candidate.get("observed_inventory_digest"), str)
            and _HEX_DIGEST.fullmatch(
                str(candidate.get("observed_inventory_digest"))
            )
            is not None
            and isinstance(candidate.get("driver_version_digest"), str)
            and _VERSION_DIGEST.fullmatch(str(candidate.get("driver_version_digest")))
            is not None
            and isinstance(candidate.get("health_url"), str)
            and str(candidate.get("health_url")).startswith("http://127.0.0.1:")
            and isinstance(candidate.get("attestation_path"), str)
            and str(candidate.get("attestation_path")).startswith(
                "/var/lib/gludd/model-workers/attestations/"
            )
        )

    @staticmethod
    def _configure_extravars(
        policy: ModelWorkerLifecyclePolicy,
    ) -> dict[str, object]:
        return {
            "gludd_model_worker_plan": policy.launch_plan.to_dict(),
            "gludd_model_worker_attestation_backend": policy.backend,
            "gludd_model_worker_attestation_minimum_memory_mib": (
                policy.minimum_memory_mib
            ),
            "gludd_model_worker_attestation_required_interconnect": (
                policy.required_interconnect
            ),
            "gludd_model_worker_attestation_expected_topology_digest": (
                policy.topology_digest
            ),
            "gludd_model_worker_attestation_runtime_probe": list(
                policy.runtime_probe
            ),
            "gludd_model_worker_attestation_expected_runtime_version_digest": (
                policy.expected_runtime_version_digest
            ),
            "gludd_model_worker_attestation_timeout_seconds": min(
                policy.configure_timeout_seconds,
                300,
            ),
            "gludd_model_worker_release_id": policy.release_id,
            "gludd_model_worker_expected_topology_digest": policy.topology_digest,
        }

    def configure(
        self,
        deployment: ProvisionedModelWorkerPool,
        policy: ModelWorkerLifecyclePolicy,
    ) -> tuple[ModelWorkerEndpoint, ...]:
        """Run attestation then launch roles and return exact ready endpoints."""
        if not isinstance(deployment, ProvisionedModelWorkerPool) or not isinstance(
            policy,
            ModelWorkerLifecyclePolicy,
        ):
            raise ValueError("configure requires deployment and lifecycle policy")
        result, aliases = self._run(
            playbook_name="model_worker_deploy.yml",
            deployment=deployment,
            extravars=self._configure_extravars(policy),
            timeout_seconds=policy.configure_timeout_seconds,
        )
        candidates = self._candidate_facts(result, aliases)
        if set(candidates) != {host.host_id for host in deployment.hosts}:
            raise ModelWorkerConfigurationError("attestation")
        endpoints: list[ModelWorkerEndpoint] = []
        for host in deployment.hosts:
            candidate = candidates[host.host_id]
            if not self._candidate_matches_policy(candidate, policy):
                raise ModelWorkerConfigurationError("attestation")
            endpoints.append(
                ModelWorkerEndpoint(
                    host_id=host.host_id,
                    endpoint_url=host.endpoint_url,
                    service_name=str(candidate["service_name"]),
                    attestation_digest=self._attestation_digest(candidate),
                )
            )
        return tuple(endpoints)

    def retire(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> None:
        """Retire the one explicit prior generation across its exact hosts."""
        if not isinstance(deployment, ProvisionedModelWorkerPool):
            raise ValueError("deployment must be ProvisionedModelWorkerPool")
        if (
            not isinstance(endpoints, tuple)
            or not endpoints
            or any(not isinstance(endpoint, ModelWorkerEndpoint) for endpoint in endpoints)
            or {endpoint.host_id for endpoint in endpoints}
            != {host.host_id for host in deployment.hosts}
        ):
            raise ModelWorkerConfigurationError("retire_contract")
        service_names = {endpoint.service_name for endpoint in endpoints}
        if len(service_names) != 1:
            raise ModelWorkerConfigurationError("retire_contract")
        service_name = next(iter(service_names))
        if _SERVICE_NAME.fullmatch(service_name) is None:
            raise ModelWorkerConfigurationError("retire_contract")
        self._run(
            playbook_name="model_worker_retire.yml",
            deployment=deployment,
            extravars={"gludd_model_worker_retire_service": service_name},
            timeout_seconds=self._retire_timeout_seconds,
        )


__all__ = (
    "AnsibleModelWorkerConfigurationRuntime",
    "ModelWorkerConfigurationError",
)
