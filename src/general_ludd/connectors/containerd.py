"""Containerd telemetry connector.

Self-contained source for container state, logs, and cgroup metrics from a
containerd-managed node. Two acquisition paths, both path-confined and
shell-free:

1. An injected ``runner`` that executes ``crictl --runtime-endpoint <sock>``
   subcommands (``ps``/``logs``/``stats``) with ``-o json``. The runner is given
   an *argv list* (never a string) so there is no shell interpretation; the
   runtime-endpoint socket path is validated to live under an allow-listed set of
   parent directories before any invocation.
2. A path-confined read of ``/var/log/pods/<ns>_<pod>_<uid>/<container>/*.log``
   CRI container log files, used as a fallback (or primary, when no runner is
   supplied).

The connector never raises out of :meth:`health` and emits normalized records
with the canonical fields ``ts, source, kind, level_or_status, message, value,
labels, raw``.

"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# An injected runner takes an argv list (+ optional timeout) and returns the
# captured stdout as text. No shell, ever.
Runner = Callable[..., str]

# Allow-listed parent directories a crictl runtime-endpoint socket may live under.
_DEFAULT_SOCK_DIRS: tuple[str, ...] = (
    "/run",
    "/var/run",
)

# Allow-listed root for CRI pod log files.
_DEFAULT_POD_LOG_ROOT = "/var/log/pods"

# CRI log line format: "<rfc3339nano> <stream> <tag> <message>".
_CRI_LOG_RE = re.compile(
    r"^(?P<ts>\S+)\s+(?P<stream>stdout|stderr)\s+(?P<tag>\S+)\s?(?P<msg>.*)$"
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _norm_ts(value: Any) -> str:
    """Coerce a timestamp-ish value to an ISO-8601 string, falling back to now."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return _utcnow_iso()
    return _utcnow_iso()


@dataclass
class ContainerdConfig:
    """Configuration for :class:`ContainerdSource`.

    All values are plain config; secrets (if any) are resolved from the
    environment via ``*_env`` indirection, never inlined.
    """

    runtime_endpoint: str = "/run/containerd/containerd.sock"
    pod_log_root: str = _DEFAULT_POD_LOG_ROOT
    allowed_sock_dirs: Sequence[str] = field(default_factory=lambda: _DEFAULT_SOCK_DIRS)
    # When set, the value of this env var is appended to crictl argv as an
    # auth/token flag value (config-driven secret handling demo; never inlined).
    auth_token_env: str | None = None
    timeout_seconds: float = 10.0
    tail_lines: int = 200
    use_runner: bool = True

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> ContainerdConfig:
        config = dict(config or {})
        kwargs: dict[str, Any] = {}
        for fname in (
            "runtime_endpoint",
            "pod_log_root",
            "auth_token_env",
            "timeout_seconds",
            "tail_lines",
            "use_runner",
        ):
            if fname in config and config[fname] is not None:
                kwargs[fname] = config[fname]
        if config.get("allowed_sock_dirs"):
            kwargs["allowed_sock_dirs"] = tuple(config["allowed_sock_dirs"])
        return cls(**kwargs)


class ContainerdSource:
    """Containerd logs + cgroup-metrics connector.

    :param config: a :class:`ContainerdConfig` or a plain dict.
    :param runner: injected argv runner (for tests / DI). When omitted and
        ``use_runner`` is true, no real subprocess is spawned — the connector
        falls back to the path-confined pod-log reader.
    """

    KIND = "logs"  # primary kind; metrics records also carry kind="metrics"
    name = "containerd"

    def __init__(
        self,
        config: ContainerdConfig | dict[str, Any] | None = None,
        *,
        runner: Runner | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if isinstance(config, ContainerdConfig):
            self.config = config
        else:
            self.config = ContainerdConfig.from_dict(config)
        self._runner = runner
        self._env = env if env is not None else os.environ

    # ---- secret resolution -------------------------------------------------
    def _auth_token(self) -> str | None:
        env_name = self.config.auth_token_env
        if not env_name:
            return None
        return self._env.get(env_name)

    # ---- path / socket confinement -----------------------------------------
    def _validate_sock(self, sock: str) -> Path:
        """Return the confined socket Path or raise ValueError.

        The endpoint may be given bare or with a ``unix://`` scheme. After
        normalization it must resolve under one of the allow-listed parent
        directories (defends against ``../`` traversal and absolute escapes).
        """
        raw = sock
        if raw.startswith("unix://"):
            raw = raw[len("unix://") :]
        if not raw.startswith("/"):
            raise ValueError(f"runtime endpoint must be an absolute path: {sock!r}")
        # Normalize without touching the filesystem (sockets may not be stat-able
        # in tests) — os.path.normpath collapses ".." and "." segments.
        norm = Path(os.path.normpath(raw))
        for parent in self.config.allowed_sock_dirs:
            pnorm = Path(os.path.normpath(parent))
            if norm == pnorm or pnorm in norm.parents:
                return norm
        raise ValueError(
            f"runtime endpoint {norm} is outside allowed dirs "
            f"{tuple(self.config.allowed_sock_dirs)}"
        )

    def _confined_pod_log_path(self, rel: str) -> Path:
        """Resolve a pod-log relative path, confined under the pod_log_root."""
        root = Path(os.path.normpath(self.config.pod_log_root))
        candidate = Path(os.path.normpath(str(root / rel)))
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"pod log path {candidate} escapes root {root}")
        return candidate

    # ---- crictl argv builders (argv lists; no shell) -----------------------
    def _base_argv(self, sock: Path, *sub: str) -> list[str]:
        argv = ["crictl", "--runtime-endpoint", f"unix://{sock}", *sub]
        token = self._auth_token()
        if token:
            argv += ["--auth-token", token]
        return argv

    def _run(self, argv: Sequence[str]) -> str:
        if self._runner is None:
            raise RuntimeError("no runner injected")
        return self._runner(list(argv), timeout=self.config.timeout_seconds)

    # ---- health ------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Never raises. Returns {'ok': bool, 'detail': str}."""
        try:
            sock = self._validate_sock(self.config.runtime_endpoint)
        except ValueError as exc:
            return {"ok": False, "detail": f"socket-confinement: {exc}"}

        if self._runner is not None and self.config.use_runner:
            try:
                out = self._run(self._base_argv(sock, "version", "-o", "json"))
                if out.strip():
                    json.loads(out)
                return {"ok": True, "detail": f"crictl reachable at {sock}"}
            except Exception as exc:  # health never raises
                return {"ok": False, "detail": f"crictl unreachable: {exc}"}

        # Pod-log fallback: the root must exist and be a directory.
        root = Path(os.path.normpath(self.config.pod_log_root))
        if root.is_dir():
            return {"ok": True, "detail": f"pod-log root present: {root}"}
        return {"ok": False, "detail": f"pod-log root missing: {root}"}

    # ---- query -------------------------------------------------------------
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return normalized records.

        Spec keys: ``what`` in {'ps','logs','stats','pod_logs','all'}, plus
        optional 'container', 'pod', 'namespace', 'pod_log_rel'.
        """
        spec = dict(spec or {})
        what = spec.get("what", "all")
        records: list[dict[str, Any]] = []

        if self._runner is not None and self.config.use_runner:
            sock = self._validate_sock(self.config.runtime_endpoint)
            if what in ("ps", "all"):
                records += self._query_ps(sock)
            if what in ("logs", "all"):
                records += self._query_logs(sock, spec)
            if what in ("stats", "all"):
                records += self._query_stats(sock)
        else:
            if what in ("pod_logs", "logs", "all"):
                records += self._query_pod_logs(spec)

        return records

    # ---- crictl-backed queries --------------------------------------------
    def _query_ps(self, sock: Path) -> list[dict[str, Any]]:
        out = self._run(self._base_argv(sock, "ps", "-a", "-o", "json"))
        data = json.loads(out) if out.strip() else {}
        recs: list[dict[str, Any]] = []
        for c in data.get("containers", []):
            meta = c.get("metadata", {}) or {}
            labels_in = c.get("labels", {}) or {}
            state = c.get("state", "")
            recs.append(
                self._record(
                    kind="logs",
                    source=f"crictl:{sock}",
                    level_or_status=state,
                    message=f"container {meta.get('name', '?')} state={state}",
                    value=None,
                    labels={
                        "pod": labels_in.get("io.kubernetes.pod.name", ""),
                        "container": meta.get("name", ""),
                        "namespace": labels_in.get("io.kubernetes.pod.namespace", ""),
                    },
                    raw=c,
                    ts=_norm_ts(c.get("createdAt")),
                )
            )
        return recs

    def _query_logs(self, sock: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
        cid = spec.get("container")
        if not cid:
            return []
        argv = self._base_argv(
            sock, "logs", "--tail", str(self.config.tail_lines), str(cid)
        )
        out = self._run(argv)
        recs: list[dict[str, Any]] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            recs.append(
                self._parse_cri_log_line(
                    line,
                    source=f"crictl-logs:{cid}",
                    labels={
                        "container": str(cid),
                        "pod": spec.get("pod", ""),
                        "namespace": spec.get("namespace", ""),
                    },
                )
            )
        return recs

    def _query_stats(self, sock: Path) -> list[dict[str, Any]]:
        out = self._run(self._base_argv(sock, "stats", "-a", "-o", "json"))
        data = json.loads(out) if out.strip() else {}
        recs: list[dict[str, Any]] = []
        for s in data.get("stats", []):
            attrs = s.get("attributes", {}) or {}
            meta = attrs.get("metadata", {}) or {}
            labels_in = attrs.get("labels", {}) or {}
            cpu = ((s.get("cpu") or {}).get("usageCoreNanoSeconds") or {}).get("value")
            mem = ((s.get("memory") or {}).get("workingSetBytes") or {}).get("value")
            base_labels = {
                "pod": labels_in.get("io.kubernetes.pod.name", ""),
                "container": meta.get("name", ""),
                "namespace": labels_in.get("io.kubernetes.pod.namespace", ""),
            }
            if cpu is not None:
                recs.append(
                    self._record(
                        kind="metrics",
                        source=f"crictl-stats:{sock}",
                        level_or_status="ok",
                        message="cgroup cpu usageCoreNanoSeconds",
                        value=_to_float(cpu),
                        labels={**base_labels, "metric": "cpu_usage_core_ns"},
                        raw=s,
                        ts=_norm_ts((s.get("cpu") or {}).get("timestamp")),
                    )
                )
            if mem is not None:
                recs.append(
                    self._record(
                        kind="metrics",
                        source=f"crictl-stats:{sock}",
                        level_or_status="ok",
                        message="cgroup memory workingSetBytes",
                        value=_to_float(mem),
                        labels={**base_labels, "metric": "memory_working_set_bytes"},
                        raw=s,
                        ts=_norm_ts((s.get("memory") or {}).get("timestamp")),
                    )
                )
        return recs

    # ---- pod-log file backed query ----------------------------------------
    def _query_pod_logs(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        rel = spec.get("pod_log_rel")
        if not rel:
            return []
        path = self._confined_pod_log_path(str(rel))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [
                self._record(
                    kind="logs",
                    source=f"podlog:{path}",
                    level_or_status="error",
                    message=f"cannot read pod log: {exc}",
                    value=None,
                    labels={
                        "pod": spec.get("pod", ""),
                        "container": spec.get("container", ""),
                        "namespace": spec.get("namespace", ""),
                    },
                    raw={"path": str(path), "error": str(exc)},
                    ts=_utcnow_iso(),
                )
            ]
        recs: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            recs.append(
                self._parse_cri_log_line(
                    line,
                    source=f"podlog:{path}",
                    labels={
                        "pod": spec.get("pod", ""),
                        "container": spec.get("container", ""),
                        "namespace": spec.get("namespace", ""),
                    },
                )
            )
        return recs

    # ---- helpers -----------------------------------------------------------
    def _parse_cri_log_line(
        self, line: str, *, source: str, labels: dict[str, str]
    ) -> dict[str, Any]:
        m = _CRI_LOG_RE.match(line)
        if m:
            stream = m.group("stream")
            return self._record(
                kind="logs",
                source=source,
                level_or_status="error" if stream == "stderr" else "info",
                message=m.group("msg"),
                value=None,
                labels={**labels, "stream": stream},
                raw={"line": line},
                ts=_norm_ts(m.group("ts")),
            )
        return self._record(
            kind="logs",
            source=source,
            level_or_status="info",
            message=line,
            value=None,
            labels=dict(labels),
            raw={"line": line},
            ts=_utcnow_iso(),
        )

    def _record(
        self,
        *,
        kind: str,
        source: str,
        level_or_status: str,
        message: str,
        value: float | None,
        labels: dict[str, Any],
        raw: Any,
        ts: str,
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": source,
            "kind": kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
