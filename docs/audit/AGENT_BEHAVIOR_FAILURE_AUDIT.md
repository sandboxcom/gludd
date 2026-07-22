# Agent Behavior Failure Audit

This file is intentionally scanned by make check-no-prompt-prone-edit-tools.

Policy:
- Agent-facing instructions must require make-backed edit targets for file writes.
- Tool names that trigger interactive edit approval must not be added to policy, plugin, or guardrail text.
- New edit helpers must be exposed through reusable Makefile targets and covered by tests before use.

Evidence:
- make gate and make gate-lite include check-no-prompt-prone-edit-tools.
- tests/unit/test_make_edit_targets_guardrails.py verifies default scanning and gate wiring.
