"""Contract tests for per-attempt stop challenges."""

from pathlib import Path

IMPL = Path(__file__).parents[2] / ".opencode/plugin/impl/enforce_stop_impl.ts"


def test_stop_denial_uses_cryptographic_challenge_tokens() -> None:
    source = IMPL.read_text(encoding="utf-8")
    assert 'from "node:crypto"' in source
    assert "randomUUID" in source
    assert "STOP CHALLENGE" in source


def test_stop_denial_persists_challenge_for_audit() -> None:
    source = IMPL.read_text(encoding="utf-8")
    assert "challenge_token" in source
    assert "stop-challenge" in source
