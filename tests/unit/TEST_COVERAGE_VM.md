# VM Sandbox Test Coverage

| File | Path | Level | Tests | Purpose |
|------|------|-------|-------|---------|
| `test_vm_sandbox_backends.py` | `tests/unit/` | Unit | 22 | Protocol compliance, import checks, fail-open behavior, auto-detection chain for Firecracker + gVisor + image_builder + agent_executor (P1 stubs) |
| `test_vm_sandbox_overhead.py` | `tests/bench/` | Bench | 6 | Dispatch-loop throughput (100 agents), boot-time estimation, image-builder timing, agent-executor throughput (200 calls), backend-selection overhead across 4 platform profiles, Firecracker/gVisor latency parity |
| `test_vm_sandbox_integration.py` | `tests/integration/` | Integration | 46 | Full apply→verify→release lifecycle (Firecracker + gVisor), auto-detection chain with fallback ordering, 5-thread concurrent backend access (Firecracker, gVisor, mixed), PermissionSpec integration, SandboxTarget variants (pid/directory/service), AgentExecutor + ImageBuilder round-trip |
