"""Slurm job adapter: submit via sbatch, query via sacct, cancel via scancel."""

from __future__ import annotations

import enum
import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class SlurmNotInstalledError(Exception):
    """Raised when Slurm commands are not available on the system."""


class SlurmJobState(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    NODE_FAIL = "NODE_FAIL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_string(cls, raw: str) -> SlurmJobState:
        raw = raw.strip().upper()
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@dataclass
class SlurmJobInfo:
    job_id: str
    state: SlurmJobState
    exit_code: int | None = None


class SlurmAdapter:
    def submit(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        output: str | None = None,
        extra_args: list[str] | None = None,
    ) -> str:
        args = ["sbatch"]
        if extra_args:
            args.extend(extra_args)
        script = self._build_script(
            command=command,
            job_name=job_name,
            partition=partition,
            cpus_per_task=cpus_per_task,
            gpus=gpus,
            memory=memory,
            time_limit=time_limit,
            output=output,
        )
        args.append(script)

        try:
            result = subprocess.run(
                args,
                stdin=subprocess.PIPE,
                input=script.encode(),
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("sbatch not found on PATH") from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"sbatch failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        return self._parse_job_id(result.stdout)

    def status(self, job_id: str) -> SlurmJobInfo:
        args = [
            "sacct",
            "--format=JobID,State,ExitCode",
            "--parsable2",
            "--noheader",
            "--jobs",
            job_id,
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("sacct not found on PATH") from exc

        if not result.stdout.strip():
            return SlurmJobInfo(job_id=job_id, state=SlurmJobState.UNKNOWN)

        return self._parse_sacct_line(job_id, result.stdout.strip())

    def cancel(self, job_id: str) -> None:
        try:
            result = subprocess.run(
                ["scancel", job_id],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("scancel not found on PATH") from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"scancel failed (rc={result.returncode}): {result.stderr.strip()}"
            )

    def available(self) -> bool:
        try:
            result = subprocess.run(
                ["sbatch", "--version"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _build_script(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        output: str | None = None,
    ) -> str:
        lines = ["#!/bin/bash"]
        if job_name:
            lines.append(f"#SBATCH --job-name={job_name}")
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        if cpus_per_task is not None:
            lines.append(f"#SBATCH --cpus-per-task={cpus_per_task}")
        if gpus:
            lines.append(f"#SBATCH --gres=gpu:{gpus}")
        if memory:
            lines.append(f"#SBATCH --mem={memory}")
        if time_limit:
            lines.append(f"#SBATCH --time={time_limit}")
        if output:
            lines.append(f"#SBATCH --output={output}")
        lines.append("")
        lines.append(command)
        return "\n".join(lines)

    @staticmethod
    def _parse_job_id(stdout: str) -> str:
        for line in stdout.strip().splitlines():
            line = line.strip()
            if "Submitted batch job" in line:
                return line.split()[-1]
        raise RuntimeError(f"Could not parse job ID from sbatch output: {stdout!r}")

    @staticmethod
    def _parse_sacct_line(job_id: str, line: str) -> SlurmJobInfo:
        parts = line.split("|")
        state_str = parts[1] if len(parts) > 1 else "UNKNOWN"
        exit_code_raw = parts[2] if len(parts) > 2 else None

        state = SlurmJobState.from_string(state_str)
        exit_code: int | None = None
        if exit_code_raw and exit_code_raw.strip():
            exit_code = int(exit_code_raw.strip())

        return SlurmJobInfo(job_id=job_id, state=state, exit_code=exit_code)
