# Agent Behavior Failure Audit

This file is intentionally scanned by make check-no-prompt-prone-edit-tools.

Policy:

- Agent-facing instructions must require make-backed edit targets for file writes.
- Tool names that trigger interactive edit approval must not be added to policy, plugin, or guardrail text.
- New edit helpers must be exposed through reusable Makefile targets and covered by tests before use.
- The execution-wrapper boundary must reject nested calls to prompt-prone internal edit tools, not only direct tool calls.

Evidence:

- make gate and make gate-lite include check-no-prompt-prone-edit-tools.
- tests/unit/test_make_edit_targets_guardrails.py verifies default scanning and gate wiring.
- tests/unit/test_enforcement_runtime_invoke.py exercises direct and nested edit denial through the real plugin runtime.
- OpenAI Codex issue [#21117](https://github.com/openai/codex/issues/21117) reports a sandbox retry path that can request duplicate approval and leave an app-server turn waiting indefinitely.
- OpenAI Codex issue [#24592](https://github.com/openai/codex/issues/24592) reports indefinite internal-edit stalls while ordinary shell-backed writes continue to work.

## 2026-08-30 nested edit incident

Two repository edits were incorrectly routed through a nested internal edit call inside the execution wrapper. They stalled for approximately 31 and 76 minutes. The existing policy blocked the direct tool name, but did not inspect execution-wrapper source for the nested call.

The regression was written first and failed against the old policy. The policy now recognizes both dot and bracket forms of the nested call and denies them before execution. Repository changes use the non-interactive Make-backed text targets.

### Zero-downtime deployment

This is a policy-only change. Existing Make-backed editing remains available throughout rollout, so in-flight work does not lose its write path. Rebuilding the hot modules and restarting OpenCode activates the strengthened runtime hook without interrupting Gludd application services.

### Rollback

Revert the policy commit and rebuild the hot modules. The Make-backed edit targets remain unchanged and are the supported recovery path.

### Resources

The check adds one bounded regular-expression match over the serialized execution-tool input. It launches no process, holds no file descriptor, creates no temporary artifact, and owns no service lifecycle.
