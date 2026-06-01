"""Terraform/OpenTofu deployment lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.infra.compute import ComputeConfig, ComputeInstance
from general_ludd.infra.terraform import TerraformGenerator

logger = logging.getLogger(__name__)


class DeploymentManager:
    def __init__(
        self,
        binary_paths: BinaryPathResolver | None = None,
        working_dir: str | None = None,
    ) -> None:
        self._binary_resolver = binary_paths or BinaryPathResolver()
        self._working_dir = working_dir or tempfile.mkdtemp(prefix="gludd-tf-")
        self._generator = TerraformGenerator()

    async def deploy(self, config: ComputeConfig) -> ComputeInstance:
        hcl = self._generator.generate(config)
        main_tf_path = os.path.join(self._working_dir, "main.tf")
        os.makedirs(self._working_dir, exist_ok=True)
        with open(main_tf_path, "w") as f:
            f.write(hcl)

        await self._run_terraform(["init", "-input=false"])
        await self._run_terraform(["apply", "-auto-approve", "-input=false"])

        output_result = await self._run_terraform(["output", "-json"])
        parsed = self._parse_outputs(output_result.get("stdout", ""))

        instance_id = parsed.get("instance_ip", parsed.get("pod_id", "unknown"))
        return ComputeInstance(
            instance_id=instance_id,
            provider=config.provider,
            status="running",
            ip_address=parsed.get("instance_ip"),
            port=8000,
            gpu_type=config.gpu_type,
            endpoint_url=parsed.get("endpoint_url"),
        )

    async def destroy(self, instance_id: str) -> None:
        await self._run_terraform(["destroy", "-auto-approve", "-input=false"])

    async def _run_terraform(self, args: list[str]) -> dict[str, Any]:
        binary = self._binary_resolver.get_infra_binary()
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
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
