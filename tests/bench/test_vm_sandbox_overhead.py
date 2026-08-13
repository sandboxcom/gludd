"""Benchmark tests for VM sandbox overhead — Unikernel P3.

All benchmarks use mocked backends (no KVM / runsc required). Timing via
``time.perf_counter()`` — no pytest-benchmark dependency. Each test asserts
that P1-stub operations complete within bounded wall-clock time so the P2/P3
real-boot paths have a measured baseline to compare against.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget


class _LivePopen(subprocess.Popen[bytes]):
    """Concrete in-memory ``Popen`` stand-in without ``MagicMock`` recursion."""

    def __init__(
        self,
        *_args: object,
        pid: int = 4242,
        **_kwargs: object,
    ) -> None:
        self.pid = pid
        self._bench_returncode: int | None = None

    def poll(self) -> int | None:
        return self._bench_returncode

    def terminate(self) -> None:
        self._bench_returncode = 0

    def kill(self) -> None:
        self._bench_returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._bench_returncode = 0
        return self._bench_returncode

    def __del__(self) -> None:
        """Avoid the real ``Popen`` finalizer, whose state was never created."""


def _applied_firecracker_handle(
    _spec: PermissionSpec,
    _target: SandboxTarget,
) -> SandboxHandle:
    return SandboxHandle(
        backend="firecracker",
        token="gludd-bench-agent",
        applied=True,
        extra={},
    )


@pytest.fixture()
def bench_spec():
    return PermissionSpec(agent_type="bench-agent")


@pytest.fixture()
def bench_target():
    return SandboxTarget(pid=88888)


# ── a. dispatch-loop overhead (100 agents) ────────────────────────────────

def test_dispatch_loop_overhead_100_agents(bench_spec, bench_target):
    """Measure apply+verify+release overhead for 100 P1-stub agents."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    with mock.patch.object(
        FirecrackerBackend,
        "available",
        new=staticmethod(lambda: True),
    ):
        start = time.perf_counter()
        for _ in range(100):
            handle = FirecrackerBackend.apply(bench_spec, bench_target)
            FirecrackerBackend.verify(bench_spec, handle)
            FirecrackerBackend.release(handle)
        elapsed = time.perf_counter() - start

    ops_per_second = 300 / elapsed  # 3 ops per agent
    assert elapsed < 5.0, (
        f"100-agent dispatch loop took {elapsed:.4f}s — "
        f"P1 stubs should complete in <5s on any host"
    )
    assert ops_per_second > 50, (
        f"Throughput {ops_per_second:.0f} ops/s — expected >50 ops/s for P1 stubs"
    )


# ── b. boot-time estimation (apply latency) ──────────────────────────────

def test_boot_time_estimation(bench_spec, bench_target):
    """Measure apply() latency for both Firecracker and gVisor P1 stubs.

    Mocks ``available()`` → True so the apply path returns an ``applied=True``
    handle (the P2 real-boot path will be measured against this baseline).
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    results: dict[str, float] = {}

    # Firecracker
    with (
        mock.patch.object(
            FirecrackerBackend,
            "available",
            new=staticmethod(lambda: True),
        ),
        mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend._spawn_firecracker",
            new=_applied_firecracker_handle,
        ),
    ):
        start = time.perf_counter()
        handle = FirecrackerBackend.apply(bench_spec, bench_target)
        elapsed_fc = time.perf_counter() - start
    assert handle.applied is True
    results["firecracker"] = elapsed_fc

    # gVisor
    with (
        mock.patch.object(
            GvisorBackend,
            "available",
            new=staticmethod(lambda: True),
        ),
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.subprocess.Popen",
            new=_LivePopen,
        ),
    ):
        start = time.perf_counter()
        handle = GvisorBackend.apply(bench_spec, bench_target)
        elapsed_gv = time.perf_counter() - start
    assert handle.applied is True
    results["gvisor"] = elapsed_gv

    for name, t in results.items():
        assert t < 0.5, (
            f"{name} apply() took {t*1000:.2f}ms — P1 stub should be <500ms"
        )


# ── c. memory / image-builder measurement ─────────────────────────────────

def test_memory_measurement_estimation(tmp_path):
    """Verify image_builder.build_rootfs() + verify_image() with mocked paths.

    Measures the wall-clock time for rootfs building (P1 stub) to establish
    a baseline for the P2 Alpine minirootfs download + ext4 creation path.
    """
    from general_ludd.security.sandboxes.vm.image_builder import (
        CACHE_DIR,
        build_rootfs,
        verify_image,
    )

    rootfs_path = tmp_path / "rootfs.ext4"

    start = time.perf_counter()
    result = build_rootfs(rootfs_path)
    build_time = time.perf_counter() - start

    assert result.path == rootfs_path
    assert build_time < 1.0, (
        f"build_rootfs stub took {build_time*1000:.2f}ms — "
        f"P1 stub should be <1s"
    )

    # build_rootfs copies a cache directory tree here (directory, not a file)
    if rootfs_path.is_dir():
        (rootfs_path / "config.json").write_text(
            '{"ociVersion": "1.1.0", "process": {"args": ["test"]}}'
        )
        (rootfs_path / "rootfs").mkdir(exist_ok=True)
    else:
        rootfs_path.write_text("stub rootfs")

    start = time.perf_counter()
    assert verify_image(rootfs_path) is True
    verify_time = time.perf_counter() - start

    assert verify_time < 0.1, (
        f"verify_image stub took {verify_time*1000:.2f}ms — "
        f"should be near-instant for existing file"
    )

    start = time.perf_counter()
    assert verify_image(tmp_path / "nonexistent.img") is False
    missing_verify_time = time.perf_counter() - start

    assert missing_verify_time < 0.1, (
        f"verify_image(missing) took {missing_verify_time*1000:.2f}ms — "
        f"should be near-instant"
    )

    assert CACHE_DIR is not None


# ── c2. image_builder build-time benchmark (mocked file I/O) ──────────────

def test_image_builder_build_time_mocked_io(tmp_path: Path) -> None:
    """Benchmark ``build_rootfs()`` wall-clock time with all file I/O mocked.

    P1 ``build_rootfs`` calls ``CACHE_DIR.mkdir()`` — the only disk operation.
    Mocking it isolates the pure compute overhead (path resolution, logging)
    so the benchmark reflects algorithmic cost rather than OS/filesystem jitter.
    """
    from general_ludd.security.sandboxes.vm.image_builder import build_rootfs

    rootfs_path = tmp_path / "bench_rootfs.ext4"
    iteration_count = 10

    with mock.patch.object(
        Path,
        "mkdir",
        new=lambda _self, *args, **kwargs: None,
    ):
        start = time.perf_counter()
        for _ in range(iteration_count):
            result = build_rootfs(rootfs_path)
        elapsed = time.perf_counter() - start

    calls_per_second = iteration_count / elapsed
    per_call_us = (elapsed / iteration_count) * 1_000_000

    assert elapsed < 5.0, (
        f"{iteration_count} build_rootfs() calls with mocked I/O took "
        f"{elapsed:.4f}s — P1 stub overhead should be <5s for {iteration_count} iters"
    )
    assert calls_per_second > 2, (
        f"Throughput {calls_per_second:.0f} calls/s — "
        f"expected >2 calls/s for P1 stub with mocked I/O ({per_call_us:.0f} µs/call)"
    )
    assert result.path == rootfs_path


# ── d. agent-executor throughput ──────────────────────────────────────────

def test_agent_executor_throughput(bench_target):
    """Measure receive_and_execute() throughput for N calls (P1 stub)."""
    from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor

    n_calls = 200

    start = time.perf_counter()
    for _ in range(n_calls):
        result = AgentExecutor.receive_and_execute(bench_target)
        assert result == {
            "exit_code": 0,
            "stdout": b"",
            "stderr": b"",
            "wall_time_s": 0.0,
            "stub": True,
        }
    elapsed = time.perf_counter() - start

    calls_per_second = n_calls / elapsed
    assert elapsed < 5.0, (
        f"{n_calls} receive_and_execute() calls took {elapsed:.4f}s — "
        f"P1 stub should complete in <5s"
    )
    assert calls_per_second > 40, (
        f"Throughput {calls_per_second:.0f} calls/s — expected >40 calls/s"
    )


# ── e. backend-selection overhead ─────────────────────────────────────────

def test_backend_selection_overhead():
    """Measure auto-detection chain overhead with various mock profiles.

    The detection chain in ``detect.auto()`` should resolve quickly when
    backends are either clearly present or absent. This establishes a
    baseline for the P2 detection path where live Firecracker/gVisor
    readiness probes will be added.
    """
    from general_ludd.security.sandboxes import detect

    def _time_auto() -> float:
        start = time.perf_counter()
        detect.auto()
        return time.perf_counter() - start

    # ── Profile: Linux + firecracker ──
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch("os.path.exists", new=lambda _path: True), \
         mock.patch("os.access", new=lambda *_args, **_kwargs: True), \
         mock.patch("shutil.which", new=lambda _name: "/usr/bin/firecracker"):
        fc_time = _time_auto()
    assert fc_time < 5.0, (
        f"auto() with firecracker available took {fc_time*1000:.2f}ms — "
        f"should complete in <5s (immediate import+check)"
    )

    # ── Profile: Linux + gVisor (no firecracker) ──
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch("os.path.exists", new=lambda _path: False), \
         mock.patch("shutil.which", new=lambda x: "/usr/bin/runsc" if x == "runsc" else None):
        gv_time = _time_auto()
    assert gv_time < 5.0, (
        f"auto() with gVisor available took {gv_time*1000:.2f}ms — "
        f"should complete in <5s"
    )

    # ── Profile: Linux + nothing available ──
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", new=lambda: False), \
         mock.patch.object(detect, "_bubblewrap_present", new=lambda: False), \
         mock.patch.object(detect, "_apparmor_enabled", new=lambda: False), \
         mock.patch.object(detect, "_selinux_enabled", new=lambda: False), \
         mock.patch("os.path.exists", new=lambda _path: False), \
         mock.patch("shutil.which", new=lambda _name: None):
        none_time = _time_auto()
    assert none_time < 5.0, (
        f"auto() with no backend available took {none_time*1000:.2f}ms — "
        f"should complete in <5s"
    )

    # ── Profile: macOS (darwin) ──
    with mock.patch.object(detect.sys, "platform", "darwin"), \
         mock.patch("shutil.which", new=lambda _name: "/usr/bin/sandbox-exec"):
        mac_time = _time_auto()
    assert mac_time < 5.0, (
        f"auto() on macOS took {mac_time*1000:.2f}ms — "
        f"should complete in <5s"
    )


# ── Assert reasonable relative ordering ──────────────────────────────────
# Firecracker stubs should be roughly as fast as gVisor stubs (both are
# P1 stubs doing string formatting + dict ops, no I/O).

def test_firecracker_and_gvisor_stub_latency_parity(bench_spec, bench_target):
    """Assert P1 stub latencies are within 10x of each other.

    Both backends do equivalent work in P1 (format a token, check a flag,
    return a dataclass). Their latencies should be in the same order of
    magnitude — a >10x difference suggests a regression or OS-level I/O
    that shouldn't exist at P1.
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch.object(
        FirecrackerBackend,
        "available",
        new=staticmethod(lambda: True),
    ):
        t0 = time.perf_counter()
        for _ in range(100):
            FirecrackerBackend.apply(bench_spec, bench_target)
        fc_total = time.perf_counter() - t0

    with mock.patch.object(
        GvisorBackend,
        "available",
        new=staticmethod(lambda: True),
    ):
        t0 = time.perf_counter()
        for _ in range(100):
            GvisorBackend.apply(bench_spec, bench_target)
        gv_total = time.perf_counter() - t0

    # Both should be > 0 (actual work happened) and within 10x of each other
    assert fc_total > 0 and gv_total > 0, "Both backends must perform measurable work"
    ratio = max(fc_total, gv_total) / min(fc_total, gv_total)
    assert ratio < 10.0, (
        f"Firecracker vs gVisor latency ratio {ratio:.1f}x exceeds 10x — "
        f"P1 stubs should have comparable overhead (fc={fc_total*1000:.2f}ms "
        f"gv={gv_total*1000:.2f}ms for 100 calls)"
    )


# ── f. verify() overhead per call ─────────────────────────────────────────
# Spec §7 requires the bench to quantify each lifecycle stage distinctly.
# ``test_dispatch_loop_overhead_100_agents`` rolls apply+verify+release into
# one loop; these next two tests isolate ``verify`` and ``release`` so a
# regression in either stage is observable on its own.

def _live_popen(pid: int = 4242) -> _LivePopen:
    """Return a concrete popen stand-in that reports a live process."""

    return _LivePopen(pid=pid)


def test_verify_overhead(bench_spec, bench_target):
    """Measure ``verify()`` per-call overhead for both backends (alive handle).

    Constructs handles whose mock popens report ``poll() is None`` (sandbox
    alive), so ``verify`` runs its full ok/warn finding-construction path
    rather than the early-exit fail path. Establishes the P1 baseline that
    the P2 real-process polling paths will be measured against.
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    n_calls = 500

    # Firecracker: verify() also checks ``os.path.exists(api_sock)`` after the
    # popen poll — mock it True so the "alive" finding branch is exercised.
    fc_handle = SandboxHandle(
        backend="firecracker",
        token="gludd-bench-verify-fc",
        applied=True,
        extra={
            "popen": _live_popen(pid=9001),
            "pid": 9001,
            "sandbox_id": "gludd-fc-verify-bench",
            "api_sock": "/tmp/gludd-fc-verify-bench.api.sock",
            "vsock_uds": "/tmp/gludd-fc-verify-bench.vsock",
            "started_at": time.time(),
        },
    )
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.os.path.exists",
        new=lambda _path: True,
    ):
        start = time.perf_counter()
        for _ in range(n_calls):
            findings = FirecrackerBackend.verify(bench_spec, fc_handle)
        fc_elapsed = time.perf_counter() - start
    assert findings and findings[-1].severity == "ok", (
        "Firecracker verify() should return an 'ok' finding for a live handle"
    )

    # gVisor: verify() only polls the popen, no FS check.
    gv_handle = SandboxHandle(
        backend="gvisor",
        token="gludd-bench-verify-gv",
        applied=True,
        extra={
            "popen": _live_popen(pid=9002),
            "pid": 9002,
            "sandbox_id": "gludd-sb-verify-bench",
            "bundle_path": "/tmp/gludd-sb-verify-bench",
            "started_at": time.time(),
        },
    )
    start = time.perf_counter()
    for _ in range(n_calls):
        findings = GvisorBackend.verify(bench_spec, gv_handle)
    gv_elapsed = time.perf_counter() - start
    assert findings and findings[-1].severity == "ok", (
        "gVisor verify() should return an 'ok' finding for a live handle"
    )

    fc_per_call_us = (fc_elapsed / n_calls) * 1_000_000
    gv_per_call_us = (gv_elapsed / n_calls) * 1_000_000

    # verify() does a popen.poll() + dict lookups + Finding construction; the
    # P1 path (no real signal handling / procfs reads) should be well under 1ms.
    assert fc_per_call_us < 1000.0, (
        f"Firecracker verify() took {fc_per_call_us:.2f}µs/call — "
        f"P1 should be <1000µs (1ms) per call"
    )
    assert gv_per_call_us < 1000.0, (
        f"gVisor verify() took {gv_per_call_us:.2f}µs/call — "
        f"P1 should be <1000µs (1ms) per call"
    )


# ── g. release() cleanup overhead per call ────────────────────────────────

def test_release_cleanup_overhead(bench_spec, bench_target):
    """Measure ``release()`` per-call cleanup overhead for both backends.

    Each release() consumes its popen (terminate→wait→unlink), so we build a
    fresh handle per iteration. Mocks the Firecracker REST PUT and FS unlink
    so the benchmark measures Python-side cleanup logic, not socket/disk I/O.
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
        _firecracker_put,
    )
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    n_calls = 200

    # Firecracker release path: PUT CtrlAltDel (mocked) → popen.poll() (None)
    # → popen.terminate() → popen.wait() → os.unlink(api_sock) (mocked).
    start = time.perf_counter()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
        new=lambda *_args, **_kwargs: {},
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.os.path.exists",
        new=lambda _path: True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.os.unlink",
        new=lambda _path: None,
    ):
        for _ in range(n_calls):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-bench-release-fc",
                applied=True,
                extra={
                    "popen": _live_popen(pid=9101),
                    "pid": 9101,
                    "sandbox_id": "gludd-fc-release-bench",
                    "api_sock": "/tmp/gludd-fc-release-bench.api.sock",
                    "vsock_uds": "/tmp/gludd-fc-release-bench.vsock",
                    "started_at": time.time(),
                },
            )
            FirecrackerBackend.release(handle)
    fc_elapsed = time.perf_counter() - start

    # gVisor release path: popen.poll() (None) → popen.terminate() → popen.wait().
    start = time.perf_counter()
    for _ in range(n_calls):
        handle = SandboxHandle(
            backend="gvisor",
            token="gludd-bench-release-gv",
            applied=True,
            extra={
                "popen": _live_popen(pid=9102),
                "pid": 9102,
                "sandbox_id": "gludd-sb-release-bench",
                "bundle_path": "/tmp/gludd-sb-release-bench",
                "started_at": time.time(),
            },
        )
        GvisorBackend.release(handle)
    gv_elapsed = time.perf_counter() - start

    fc_per_call_us = (fc_elapsed / n_calls) * 1_000_000
    gv_per_call_us = (gv_elapsed / n_calls) * 1_000_000

    # release() does a terminate+wait on a mock popen. Under the full release
    # run this benchmark also executes with coverage instrumentation, which can
    # push MagicMock-heavy loops past the no-coverage baseline without indicating
    # real blocking I/O. Keep the bound below human-visible blocking latency.
    # The bound is loose: macOS subprocess mock overhead (~6-7ms) plus gVisor
    # popen.poll() thrashing pushes it above 2ms on non-Linux. A real block
    # on I/O would be 100s of ms, so 50ms still catches the regression.
    assert fc_per_call_us < 50000.0, (
        f"Firecracker release() took {fc_per_call_us:.2f}µs/call — "
        f"P1 cleanup should be <50000µs (50ms) per call"
    )
    assert gv_per_call_us < 50000.0, (
        f"gVisor release() took {gv_per_call_us:.2f}µs/call — "
        f"P1 cleanup should be <50000µs (50ms) per call"
    )

    # Sanity: _firecracker_put is the real symbol we mocked — guards against
    # a future rename silently making the Firecracker mock a no-op.
    assert callable(_firecracker_put)
