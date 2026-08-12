"""Slurm job adapter: submit via sbatch or REST API, query via sacct or REST, cancel via scancel or REST."""

from __future__ import annotations

import enum
import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SLURM_API_VERSION = "v0.0.40"

# A Slurm job id is one or more digits, optionally with an array/step suffix
# (``123_4``, ``123.batch``). It is never a flag, never contains whitespace or
# shell metacharacters. We require it to begin with a digit so a value like
# ``-A evil`` or ``--help`` can never be smuggled into argv as an extra option.
_JOB_ID_RE = re.compile(r"^[0-9][0-9_.+]*$")

# Names / partitions / accounts / qos: a conservative safe charset. No leading
# dash (would be read as a flag / SBATCH option), no whitespace (would split
# into extra tokens), no newlines (would inject an extra ``#SBATCH`` directive
# into the generated script), no shell metacharacters.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")

# Time limits: ``MM``, ``MM:SS``, ``HH:MM:SS``, ``D-HH``, ``D-HH:MM``,
# ``D-HH:MM:SS``. Digits, colons and a single day-dash only — never leading.
_TIME_RE = re.compile(r"^[0-9]+(-[0-9]{1,2})?(:[0-9]{1,2}){0,2}$")


def _require_job_id(job_id: str) -> str:
    """Fail-closed validation for a Slurm job id used as a positional argv arg."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise ValueError(
            f"invalid Slurm job id {job_id!r}: must be numeric "
            "(no flags, whitespace or shell metacharacters)"
        )
    return job_id


def _require_name(value: str, field_name: str) -> str:
    """Fail-closed validation for a name/partition/account/qos style token."""
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ValueError(
            f"invalid Slurm {field_name} {value!r}: must match a safe charset "
            "(no leading dash, whitespace, newlines or shell metacharacters)"
        )
    return value


def _require_time(value: str) -> str:
    """Fail-closed validation for a Slurm time limit."""
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise ValueError(
            f"invalid Slurm time limit {value!r}: expected [[D-]HH:]MM[:SS] digits only"
        )
    return value


def _require_extra_arg(value: str) -> str:
    """Fail-closed validation for an operator-supplied passthrough sbatch arg.

    Each entry is a single argv token. It must be a non-empty string with no
    newline (which could smuggle a ``#SBATCH`` directive into the script) and
    no NUL.
    """
    if (
        not isinstance(value, str)
        or not value
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        raise ValueError(f"invalid sbatch extra_arg {value!r}")
    return value


def _require_output(value: str) -> str:
    """Fail-closed validation for the ``#SBATCH --output=`` path.

    Unlike name/partition/etc. an output path is intentionally not charset-
    restricted (paths can legitimately contain ``/``, ``.``, ``%`` patterns and
    spaces). But it IS embedded into a ``#SBATCH --output=`` directive line, so a
    newline (``\\n``/``\\r``) would inject an extra ``#SBATCH`` directive into the
    generated script and a NUL would truncate the path. Reject all three, mirroring
    the ``extra_args`` newline/NUL guard. (Authenticated-admin defense-in-depth, not
    shell injection.)
    """
    if (
        not isinstance(value, str)
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        raise ValueError(f"invalid Slurm output path {value!r}: no newlines/NUL")
    return value


def _parse_elapsed(raw: str) -> float | None:
    """Parse a Slurm ``Elapsed`` time string to seconds (float).

    Accepts the formats sacct emits for ``--format=Elapsed``:
    ``HH:MM:SS``, ``D-HH:MM:SS``, ``MM:SS``, or ``HH:MM:SS.US``.
    Returns ``None`` on an unparseable / unlimited / empty input.
    """
    raw = raw.strip()
    if not raw or raw.upper() == "UNLIMITED":
        return None

    days = 0
    time_part = raw

    if "-" in raw:
        parts = raw.split("-", 1)
        try:
            days = int(parts[0])
            time_part = parts[1]
        except (ValueError, IndexError):
            return None

    time_parts = time_part.split(":")
    try:
        if len(time_parts) == 3:
            hours = int(time_parts[0])
            minutes = int(time_parts[1])
            seconds = float(time_parts[2])
        elif len(time_parts) == 2:
            hours = 0
            minutes = int(time_parts[0])
            seconds = float(time_parts[1])
        else:
            return None
    except (ValueError, IndexError):
        return None

    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


class SlurmNotInstalledError(Exception):
    """Raised when Slurm commands are not available on the system."""


class SlurmConnectionError(Exception):
    """Raised when the Slurm REST API is unreachable."""


class SlurmJobState(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    NODE_FAIL = "NODE_FAIL"
    PREEMPTED = "PREEMPTED"
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
    cost_incurred: float = 0.0
    original_job_id: str | None = None
    resubmit_count: int = 0


@dataclass
class SlurmJobConfig:
    """Parameter bundle for a Slurm job submission.

    Provides fields that map to ``--account``, ``--qos``, and ``--time``
    sbatch options.  Also carries cost / idle guardrails consumed by
    :class:`SlurmJobMonitor`.  All fields default to ``None`` (unset);
    the adapter treats ``None`` as "omit the flag / directive entirely";
    the monitor skips checks whose config field is ``None``.
    """

    account: str | None = None
    qos: str | None = None
    time_limit_str: str | None = None
    max_cost_usd: float | None = None
    idle_timeout_minutes: float | None = None
    hourly_rate_usd: float | None = None


@dataclass
class SlurmAdapter:
    _api_url: str | None = field(default=None, repr=False)
    _auth_token: str | None = field(default=None, repr=False)

    def __init__(
        self,
        api_url: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._api_url = api_url
        self._auth_token = auth_token

    @property
    def _is_remote(self) -> bool:
        return bool(self._api_url)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-SLURM-USER-NAME": "slurm",
        }
        if self._auth_token:
            headers["X-SLURM-USER-TOKEN"] = self._auth_token
        return headers

    def _api_base(self) -> str:
        url = (self._api_url or "").rstrip("/")
        return f"{url}/slurm/{_SLURM_API_VERSION}"

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue an HTTP request, converting transport failures to SlurmConnectionError.

        A raw ``httpx.ConnectError``/``TimeoutException`` leaking out of the REST
        path is indistinguishable from a programming error to callers. Wrapping
        it in :class:`SlurmConnectionError` makes "the controller is unreachable"
        an explicit, catchable condition.
        """
        m = method.upper()
        # httpx's module-level get/post/delete expose precise overloaded
        # signatures; the local **kwargs bag carries only the subset of those
        # keyword arguments (headers/json/params/timeout) supplied by internal
        # call sites, so cast to satisfy mypy without losing runtime flexibility.
        method_map: dict[str, Callable[..., httpx.Response]] = {
            "GET": httpx.get,
            "POST": httpx.post,
            "DELETE": httpx.delete,
        }
        try:
            handler = method_map.get(m)
            if handler is None:
                raise ValueError(f"unsupported method: {method}")
            return handler(url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise SlurmConnectionError(
                f"Slurm REST API unreachable at {url}: {exc}"
            ) from exc

    def submit(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        account: str | None = None,
        qos: str | None = None,
        output: str | None = None,
        extra_args: list[str] | None = None,
    ) -> str:
        # Validate every config-derived value fail-closed BEFORE it is embedded
        # in argv or in the generated batch script. This guards both the local
        # CLI path and the REST path (a hostile job_name/partition could inject
        # an extra ``#SBATCH`` directive into the script that REST also runs).
        self._validate_submit_params(
            job_name=job_name,
            partition=partition,
            gpus=gpus,
            memory=memory,
            time_limit=time_limit,
            account=account,
            qos=qos,
            output=output,
            extra_args=extra_args,
        )
        if self._is_remote:
            return self._remote_submit(
                command=command,
                job_name=job_name,
                partition=partition,
                cpus_per_task=cpus_per_task,
                gpus=gpus,
                memory=memory,
                time_limit=time_limit,
                account=account,
                qos=qos,
            )
        return self._local_submit(
            command=command,
            job_name=job_name,
            partition=partition,
            cpus_per_task=cpus_per_task,
            gpus=gpus,
            memory=memory,
            time_limit=time_limit,
            account=account,
            qos=qos,
            output=output,
            extra_args=extra_args,
        )

    @staticmethod
    def _validate_submit_params(
        *,
        job_name: str | None,
        partition: str | None,
        gpus: str | None,
        memory: str | None,
        time_limit: str | None,
        account: str | None = None,
        qos: str | None = None,
        output: str | None,
        extra_args: list[str] | None,
    ) -> None:
        """Fail-closed validation of all user/config-supplied submit params.

        ``command`` is deliberately not charset-restricted here: it is the script
        body (arbitrary shell by design). ``output`` is a path, so it is NOT
        charset-restricted either — but it IS embedded into a ``#SBATCH --output=``
        directive line, so it is checked for newlines/NUL (which would inject an
        extra directive). name/partition/gpus/memory/time_limit/account/qos are embedded
        into ``#SBATCH`` directive lines, so a newline or shell metacharacter in any
        of them would let a caller inject an extra directive or break out of the
        intended argument — exactly the injection this hardening prevents.
        """
        if job_name is not None:
            _require_name(job_name, "job_name")
        if partition is not None:
            _require_name(partition, "partition")
        if gpus is not None:
            _require_name(gpus, "gpus")
        if memory is not None:
            _require_name(memory, "memory")
        if account is not None:
            _require_name(account, "account")
        if qos is not None:
            _require_name(qos, "qos")
        if time_limit is not None:
            _require_time(time_limit)
        if output is not None:
            _require_output(output)
        if extra_args is not None:
            for arg in extra_args:
                _require_extra_arg(arg)

    def status(self, job_id: str) -> SlurmJobInfo:
        if self._is_remote:
            return self._remote_status(job_id)
        return self._local_status(job_id)

    def cancel(self, job_id: str) -> None:
        if self._is_remote:
            return self._remote_cancel(job_id)
        return self._local_cancel(job_id)

    def elapsed_seconds(self, job_id: str) -> float | None:
        if self._is_remote:
            return None
        return self._local_elapsed_seconds(job_id)

    def _local_elapsed_seconds(self, job_id: str) -> float | None:
        job_id = _require_job_id(job_id)
        args = [
            "sacct",
            "--format=JobID,Elapsed",
            "--parsable2",
            "--noheader",
            f"--jobs={job_id}",
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        if result.returncode != 0 or not result.stdout.strip():
            return None

        for line in result.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].strip() == job_id:
                return _parse_elapsed(parts[1])

        return None

    def available(self) -> bool:
        if self._is_remote:
            return self._remote_available()
        return self._local_available()

    def list_jobs(self) -> list[SlurmJobInfo]:
        if self._is_remote:
            return self._remote_list_jobs()
        return self._local_list_jobs()

    def _remote_submit(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        account: str | None = None,
        qos: str | None = None,
    ) -> str:
        script_lines = ["#!/bin/bash"]
        if job_name:
            script_lines.append(f"#SBATCH --job-name={job_name}")
        if partition:
            script_lines.append(f"#SBATCH --partition={partition}")
        if account:
            script_lines.append(f"#SBATCH --account={account}")
        if qos:
            script_lines.append(f"#SBATCH --qos={qos}")
        if cpus_per_task is not None:
            script_lines.append(f"#SBATCH --cpus-per-task={cpus_per_task}")
        if gpus:
            script_lines.append(f"#SBATCH --gres=gpu:{gpus}")
        if memory:
            script_lines.append(f"#SBATCH --mem={memory}")
        if time_limit:
            script_lines.append(f"#SBATCH --time={time_limit}")
        script_lines.append("")
        script_lines.append(command)
        script = "\n".join(script_lines)

        payload = {
            "script": script,
            "job": {
                "partition": partition or "",
                "name": job_name or "",
                "environment": {"PATH": "/bin:/usr/bin:/usr/local/bin"},
            },
        }

        resp = self._request(
            "POST",
            f"{self._api_base()}/job/submit",
            json=payload,
            headers=self._headers(),
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Slurm REST submit failed (rc={resp.status_code}): {resp.text}"
            )
        data = resp.json()
        job_id = data.get("job_id") or data.get("job_submit_user_msg", {}).get("job_id")
        if job_id is None:
            raise RuntimeError(f"Could not parse job_id from REST response: {data}")
        return str(job_id)

    def _remote_status(self, job_id: str) -> SlurmJobInfo:
        resp = self._request(
            "GET",
            f"{self._api_base()}/job/{job_id}",
            headers=self._headers(),
            timeout=15.0,
        )
        if resp.status_code == 404:
            return SlurmJobInfo(job_id=job_id, state=SlurmJobState.UNKNOWN)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Slurm REST status failed (rc={resp.status_code}): {resp.text}"
            )
        data = resp.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return SlurmJobInfo(job_id=job_id, state=SlurmJobState.UNKNOWN)
        job = jobs[0]
        state = SlurmJobState.from_string(job.get("job_state", "UNKNOWN"))
        exit_code = job.get("exit_code")
        return SlurmJobInfo(
            job_id=str(job.get("job_id", job_id)),
            state=state,
            exit_code=int(exit_code) if exit_code is not None else None,
        )

    def _remote_cancel(self, job_id: str) -> None:
        resp = self._request(
            "DELETE",
            f"{self._api_base()}/job/{job_id}",
            headers=self._headers(),
            timeout=15.0,
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"Slurm REST cancel failed (rc={resp.status_code}): {resp.text}"
            )

    def _remote_available(self) -> bool:
        try:
            url = (self._api_url or "").rstrip("/")
            resp = httpx.get(f"{url}/slurm/{_SLURM_API_VERSION}/ping", headers=self._headers(), timeout=5.0)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    def _remote_list_jobs(self) -> list[SlurmJobInfo]:
        resp = self._request(
            "GET",
            f"{self._api_base()}/jobs",
            headers=self._headers(),
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Slurm REST list failed (rc={resp.status_code}): {resp.text}"
            )
        data = resp.json()
        jobs = data.get("jobs", [])
        return [
            SlurmJobInfo(
                job_id=str(j.get("job_id", "")),
                state=SlurmJobState.from_string(j.get("job_state", "UNKNOWN")),
            )
            for j in jobs
        ]

    def _local_submit(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        account: str | None = None,
        qos: str | None = None,
        output: str | None = None,
        extra_args: list[str] | None = None,
    ) -> str:
        args = ["sbatch"]
        if extra_args:
            for arg in extra_args:
                args.append(_require_extra_arg(arg))
        script = self._build_script(
            command=command,
            job_name=job_name,
            partition=partition,
            cpus_per_task=cpus_per_task,
            gpus=gpus,
            memory=memory,
            time_limit=time_limit,
            account=account,
            qos=qos,
            output=output,
        )
        # End-of-options guard so the trailing script positional can never be
        # reinterpreted as an sbatch flag, then the script body itself.
        args.append("--")
        args.append(script)

        try:
            result = subprocess.run(
                args,
                stdin=subprocess.PIPE,
                input=script.encode(),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("sbatch not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SlurmConnectionError(
                f"sbatch timed out after {exc.timeout}s"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"sbatch failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        return self._parse_job_id(result.stdout)

    def _local_status(self, job_id: str) -> SlurmJobInfo:
        job_id = _require_job_id(job_id)
        # Bind the job id to its flag with ``--jobs=<id>`` so it can never be
        # parsed as a standalone option. sacct takes no positional args, so the
        # validated id rides as the flag value rather than as a positional.
        args = [
            "sacct",
            "--format=JobID,State,ExitCode",
            "--parsable2",
            "--noheader",
            f"--jobs={job_id}",
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("sacct not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SlurmConnectionError(
                f"sacct timed out after {exc.timeout}s"
            ) from exc

        if not result.stdout.strip():
            return SlurmJobInfo(job_id=job_id, state=SlurmJobState.UNKNOWN)

        return self._parse_sacct_line(job_id, result.stdout.strip())

    def _local_cancel(self, job_id: str) -> None:
        job_id = _require_job_id(job_id)
        try:
            result = subprocess.run(
                ["scancel", "--", job_id],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("scancel not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SlurmConnectionError(
                f"scancel timed out after {exc.timeout}s"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"scancel failed (rc={result.returncode}): {result.stderr.strip()}"
            )

    def _local_available(self) -> bool:
        try:
            result = subprocess.run(
                ["sbatch", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _local_list_jobs(self) -> list[SlurmJobInfo]:
        try:
            result = subprocess.run(
                ["squeue", "--me", "--format=%i|%T", "--noheader"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise SlurmNotInstalledError("squeue not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SlurmConnectionError(
                f"squeue timed out after {exc.timeout}s"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"squeue failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        jobs: list[SlurmJobInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 2:
                jobs.append(SlurmJobInfo(
                    job_id=parts[0].strip(),
                    state=SlurmJobState.from_string(parts[1]),
                ))
        return jobs

    def _build_script(
        self,
        command: str,
        job_name: str | None = None,
        partition: str | None = None,
        cpus_per_task: int | None = None,
        gpus: str | None = None,
        memory: str | None = None,
        time_limit: str | None = None,
        account: str | None = None,
        qos: str | None = None,
        output: str | None = None,
    ) -> str:
        lines = ["#!/bin/bash"]
        if job_name:
            lines.append(f"#SBATCH --job-name={job_name}")
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        if account:
            lines.append(f"#SBATCH --account={account}")
        if qos:
            lines.append(f"#SBATCH --qos={qos}")
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


_TERMINAL_STATES = frozenset(
    {
        SlurmJobState.COMPLETED,
        SlurmJobState.FAILED,
        SlurmJobState.CANCELLED,
        SlurmJobState.TIMEOUT,
        SlurmJobState.NODE_FAIL,
    }
)


class SlurmJobMonitor:
    """Background monitor for Slurm job cost cap and idle detection.

    Spawns a daemon thread that periodically polls ``sacct`` for elapsed
    time and optionally checks for inference request activity.  If
    accumulated cost exceeds ``max_cost_usd`` or no requests arrive within
    ``idle_timeout_minutes``, the job is cancelled via ``scancel``.

    Usage::

        monitor = SlurmJobMonitor(adapter, job_id, config)
        monitor.start()
        # ...
        monitor.stop()
    """

    CANCEL_REASON_COST = "cost_cap"
    CANCEL_REASON_IDLE = "idle_timeout"

    def __init__(
        self,
        adapter: SlurmAdapter,
        job_id: str,
        config: SlurmJobConfig,
        *,
        activity_checker: Callable[[], bool] | None = None,
        poll_interval: float = 30.0,
    ) -> None:
        self._adapter = adapter
        self._job_id = _require_job_id(job_id)
        self._config = config
        self._activity_checker = activity_checker
        self._poll_interval = poll_interval

        self._cost_incurred: float = 0.0
        self._cancelled: bool = False
        self._cancel_reason: str | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def cost_incurred(self) -> float:
        with self._lock:
            return self._cost_incurred

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def cancel_reason(self) -> str | None:
        with self._lock:
            return self._cancel_reason

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"slurm-monitor-{self._job_id}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    # -- polling loop -----------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self._poll():
                return
            self._stop_event.wait(self._poll_interval)

    def _poll(self) -> bool:
        """Execute one monitoring cycle.

        Returns:
            ``True`` if monitoring should continue, ``False`` if the
            monitor should exit (job terminal, cancelled by cap/idle).
        """
        info = self._adapter.status(self._job_id)

        if self._config.max_cost_usd is not None:
            elapsed = self._adapter.elapsed_seconds(self._job_id)
            if elapsed is not None:
                rate = self._resolve_hourly_rate()
                hours = elapsed / 3600.0
                cost = hours * rate
                with self._lock:
                    self._cost_incurred = cost

        if info.state in _TERMINAL_STATES:
            return False

        if self._config.max_cost_usd is not None:
            with self._lock:
                current_cost = self._cost_incurred
            if current_cost > self._config.max_cost_usd:
                with self._lock:
                    self._cancelled = True
                    self._cancel_reason = self.CANCEL_REASON_COST
                self._adapter.cancel(self._job_id)
                return False

        if (
            self._activity_checker is not None
            and self._config.idle_timeout_minutes is not None
        ):
            if not hasattr(self, "_idle_since"):
                self._idle_since: float | None = None
            if self._activity_checker():
                self._idle_since = None
            elif self._idle_since is None:
                self._idle_since = time.time()
            elif time.time() - self._idle_since >= self._config.idle_timeout_minutes * 60.0:
                with self._lock:
                    self._cancelled = True
                    self._cancel_reason = self.CANCEL_REASON_IDLE
                self._adapter.cancel(self._job_id)
                return False

        return True

    def _resolve_hourly_rate(self) -> float:
        if self._config.hourly_rate_usd is not None:
            return self._config.hourly_rate_usd
        from general_ludd.infra.pricing import infra_cost_usd

        return infra_cost_usd("gpu_second", 3600.0)
