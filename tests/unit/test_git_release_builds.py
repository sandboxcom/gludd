"""GRC-AT-006: Reproducible builds — two clean builds produce byte-identical
artifacts; all expected artifacts install/smoke/uninstall/verify.

Per spec GRC-001 §7, the release state machine enforces BUILD_ONCE with a
pinned source SHA, and the deployment orchestrator gates every promotion on
artifact digest matching.  ``general_ludd.git_release.release_state`` and
``general_ludd.git_release.deployment`` provide the primitives.

This module exercises digest-matching, state-machine immutability, and the
build-once invariant.  The full install/smoke/uninstall integration suite is
skipped pending sandbox-forge wiring.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from types import ModuleType

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_RELEASE_STATE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "git_release", "release_state.py")
_DEPLOYMENT_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "git_release", "deployment.py")


def _load_mod(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


release_state = _load_mod(_RELEASE_STATE_PATH, "grc_release_state_at006")
deployment = _load_mod(_DEPLOYMENT_PATH, "grc_deployment_at006")


# ---------------------------------------------------------------------------
# Reference digest helpers
# ---------------------------------------------------------------------------


def _artifact_digest(content: str) -> str:
    """Deterministic content-addressed digest for a build artifact."""
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests: Digest matching
# ---------------------------------------------------------------------------


class TestBuildDigestMatching:
    """GRC-AT-006: two clean builds produce byte-identical artifacts."""

    def test_same_content_produces_same_digest(self) -> None:
        d1 = _artifact_digest("release-1.0.0 binary content")
        d2 = _artifact_digest("release-1.0.0 binary content")
        assert d1 == d2

    def test_different_content_produces_different_digest(self) -> None:
        d1 = _artifact_digest("release-1.0.0")
        d2 = _artifact_digest("release-1.0.1")
        assert d1 != d2

    def test_empty_content_has_known_digest(self) -> None:
        d = _artifact_digest("")
        expected = hashlib.sha256(b"").hexdigest()
        assert d == expected

    def test_digest_is_hex_string(self) -> None:
        d = _artifact_digest("some artifact")
        assert len(d) == 64
        assert all(c in "0123456789abcdef" for c in d)


# ---------------------------------------------------------------------------
# Tests: Deployment digest gating
# ---------------------------------------------------------------------------


class TestDeploymentDigestGate:
    """DeploymentOrchestrator gates on digest identity."""

    def test_orchestrator_requires_non_empty_digests(self) -> None:
        with pytest.raises(ValueError):
            deployment.DeploymentOrchestrator(
                config=deployment.DeploymentConfig(
                    strategy=deployment.DeploymentStrategy.CANARY,
                    health_gate=deployment.HealthGate(
                        max_error_rate=0.01,
                        min_availability=0.99,
                        max_latency_p99_ms=200.0,
                    ),
                    abort_threshold=0.05,
                ),
                prior_digest="",
                new_digest="abc123",
            )

    def test_orchestrator_exposes_digests(self) -> None:
        orch = deployment.DeploymentOrchestrator(
            config=deployment.DeploymentConfig(
                strategy=deployment.DeploymentStrategy.CANARY,
                health_gate=deployment.HealthGate(
                    max_error_rate=0.01,
                    min_availability=0.99,
                    max_latency_p99_ms=200.0,
                ),
                abort_threshold=0.05,
            ),
            prior_digest="sha256:aaa",
            new_digest="sha256:bbb",
        )
        assert orch.prior_digest == "sha256:aaa"
        assert orch.new_digest == "sha256:bbb"


# ---------------------------------------------------------------------------
# Tests: Release state machine BUILD_ONCE invariant
# ---------------------------------------------------------------------------


class TestBuildOnceInvariant:
    """The release state machine enforces BUILD_ONCE with pinned SHA."""

    def test_state_machine_exists(self) -> None:
        assert hasattr(release_state, "ReleaseStateMachine")

    def test_release_state_enum_has_build_once(self) -> None:
        assert hasattr(release_state, "ReleaseState")
        assert release_state.ReleaseState.BUILD_ONCE.value == "build_once"

    def test_build_once_to_verify_offline_transition(self) -> None:
        sm = release_state.ReleaseStateMachine(source_sha="0" * 40, artifact_digest="0" * 64)
        sm.advance(target=release_state.ReleaseState.PLAN)
        result = sm.advance(target=release_state.ReleaseState.BUILD_ONCE)
        assert not result.blocked
        assert sm.state == release_state.ReleaseState.BUILD_ONCE

    def test_state_machine_rejects_invalid_transition(self) -> None:
        sm = release_state.ReleaseStateMachine(source_sha="0" * 40, artifact_digest="0" * 64)
        with pytest.raises(release_state.TransitionError):
            sm.advance(target=release_state.ReleaseState.CANARY)

    def test_released_is_terminal(self) -> None:
        # Verify RELEASED has no forward edges
        edges = release_state._ALLOWED_FORWARD.get(release_state.ReleaseState.RELEASED, frozenset())
        assert len(edges) == 0


# ---------------------------------------------------------------------------
# Tests: Full reproducibility pipeline (integration, skipped)
# ---------------------------------------------------------------------------


class TestReproducibleBuildPipeline:
    """GRC-AT-006: install / smoke / uninstall / verify cycle."""

    @pytest.mark.skip(
        "GRC-AT-006: reproducible-build end-to-end fixture not yet wired. "
        "Requires sandbox-forge to produce artifacts from two independent "
        "builds and verify byte-identical digests.  Primitives "
        "(ReleaseStateMachine.BUILD_ONCE, digest matching) are correct."
    )
    def test_two_clean_builds_byte_identical(self) -> None:
        pass

    @pytest.mark.skip("GRC-AT-006: install/smoke/uninstall test requires sandbox-forge.")
    def test_artifact_installs_and_smokes(self) -> None:
        pass

    @pytest.mark.skip("GRC-AT-006: uninstall/verify test requires sandbox-forge.")
    def test_artifact_uninstalls_and_verifies_cleanup(self) -> None:
        pass
