"""STS (Security Token Service) — per-subagent OpenBao AppRole token lifecycle.

Phases (see docs/specs/FEATURE_STS_TOKENS.md):
  P1 — TokenMinter + TokenStore + AgentTokenModel migration
  P2 — CapabilityNarrowing
  P3 — TokenReviver + HibernationController integration
  P4 — TokenRevoker + full audit event pipeline
"""

from __future__ import annotations
