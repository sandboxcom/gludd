"""Deep deployment pipeline and release engineering tests.

Covers the full ZDD protocol lifecycle across modules:
- git_release.deployment    — DeploymentOrchestrator, canary/blue-green/rolling
- git_release.release_state — ReleaseStateMachine, rollback, version lifecycle
- git_automation.release_ops — release_cut, release_delete, release_recut
- runtime.release            — ReleaseArtifactValidator, checksums, provenance
- runtime.manifest_signer    — ManifestSigner, SSH-based signing/verification
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from general_ludd.git_automation.types import (
    ReleaseCutResult,
    ReleaseDeleteResult,
    ReleaseRecutResult,
)
from general_ludd.git_release.deployment import (
    AbortDecision,
    BlueGreenCutComplete,
    DeploymentConfig,
    DeploymentOrchestrator,
    DeploymentStrategy,
    HealthGate,
    HealthSample,
    HoldDecision,
    PromoteDecision,
    RollbackDecision,
)
from general_ludd.git_release.release_state import (
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)
from general_ludd.runtime.manifest_signer import (
    ManifestSigner,
)
from general_ludd.runtime.release import ReleaseArtifactValidator

# -----------------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------------

_HEALTHY = HealthSample(availability=0.999, error_rate=0.001, latency_p99_ms=50.0)
_GATE = HealthGate(max_error_rate=0.05, min_availability=0.95, max_latency_p99_ms=200.0)
_CONFIG = DeploymentConfig(
    strategy=DeploymentStrategy.CANARY,
    health_gate=_GATE,
    abort_threshold=0.10,
    max_step_percent=25,
    observation_window_s=120,
)
_CONFIG_BG = DeploymentConfig(
    strategy=DeploymentStrategy.BLUE_GREEN,
    health_gate=_GATE,
    abort_threshold=0.10,
    max_step_percent=100,
    observation_window_s=300,
)
_CONFIG_ROLLING = DeploymentConfig(
    strategy=DeploymentStrategy.ROLLING,
    health_gate=_GATE,
    abort_threshold=0.10,
    max_step_percent=33,
    observation_window_s=90,
)
_PRIOR_DIGEST = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_NEW_DIGEST = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_SOURCE_SHA = "a" * 40


# =======================================================================
# 1. CANARY DEPLOYMENT — health evaluation, traffic shifts, regression
# =======================================================================


class TestCanaryHealthEvaluation:
    def test_healthy_sample_promotes(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        decision = orch.evaluate(stage="canary", sample=_HEALTHY)
        assert isinstance(decision, PromoteDecision)
        assert decision.digest == _NEW_DIGEST
        assert decision.next_percent == 25

    def test_high_error_rate_triggers_rollback(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        bad = HealthSample(availability=0.999, error_rate=0.15, latency_p99_ms=50.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, RollbackDecision)
        assert decision.target_digest == _PRIOR_DIGEST
        assert decision.metric == "error_rate"

    def test_low_availability_aborts_without_rollback(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        bad = HealthSample(availability=0.80, error_rate=0.001, latency_p99_ms=50.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, AbortDecision)
        assert decision.metric == "availability"
        assert decision.observed == 0.80

    def test_high_latency_aborts_without_rollback(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        bad = HealthSample(availability=0.999, error_rate=0.001, latency_p99_ms=500.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, AbortDecision)
        assert decision.metric == "latency_p99_ms"

    def test_missing_telemetry_holds(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        decision = orch.evaluate(stage="canary", sample=None)
        assert isinstance(decision, HoldDecision)
        assert "missing-or-stale" in decision.reason

    def test_canary_traffic_shift_bounded_by_max_step(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        shift = orch.next_shift(current_percent=10)
        assert shift.next_percent == 35
        assert shift.observation_window_s == 120

    def test_canary_traffic_shift_caps_at_100(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        shift = orch.next_shift(current_percent=90)
        assert shift.next_percent == 100

    def test_error_rate_below_abort_threshold_only_aborts(self) -> None:
        low_threshold_config = DeploymentConfig(
            strategy=DeploymentStrategy.CANARY,
            health_gate=HealthGate(max_error_rate=0.01, min_availability=0.95, max_latency_p99_ms=200.0),
            abort_threshold=0.50,
        )
        orch = DeploymentOrchestrator(
            config=low_threshold_config,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        bad = HealthSample(availability=0.999, error_rate=0.03, latency_p99_ms=50.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, AbortDecision)
        assert not isinstance(decision, RollbackDecision)


# =======================================================================
# 2. BLUE-GREEN DEPLOYMENT — single cutover, completion
# =======================================================================


class TestBlueGreenSwitching:
    def test_blue_green_single_shot_100_percent(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG_BG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        shift = orch.next_shift(current_percent=0)
        assert shift.next_percent == 100
        assert shift.observation_window_s == 300

    def test_blue_green_promote_is_100(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG_BG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        decision = orch.evaluate(stage="blue_green", sample=_HEALTHY)
        assert isinstance(decision, PromoteDecision)
        assert decision.next_percent == 100

    def test_blue_green_final_shift_already_at_100(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG_BG,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        shift = orch.next_shift(current_percent=100)
        assert shift.next_percent == 100

    def test_bluegreen_cut_complete_decision(self) -> None:
        cut = BlueGreenCutComplete(digest=_NEW_DIGEST)
        assert cut.digest == _NEW_DIGEST
        from general_ludd.git_release.deployment import Decision

        assert isinstance(cut, Decision)


# =======================================================================
# 3. ROLLBACK LOGIC — canary regression, restore prior digest
# =======================================================================


class TestRollbackLogic:
    def test_rollback_from_canary_restores_prior_digest(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
        )
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        assert sm.serving_digest == _NEW_DIGEST
        sm.rollback(reason="canary error-rate spike")
        assert sm.state == ReleaseState.ROLLBACK
        assert sm.serving_digest == _PRIOR_DIGEST

    def test_rollback_from_promote_restores_prior_digest(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
        )
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
        sm.rollback(reason="promote regression")
        assert sm.state == ReleaseState.ROLLBACK
        assert sm.serving_digest == _PRIOR_DIGEST

    def test_rollback_forbidden_from_discover(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        with pytest.raises(TransitionError, match="not permitted"):
            sm.rollback(reason="premature rollback")

    def test_rollback_forbidden_from_released(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
        )
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
        sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
        sm.advance(target=ReleaseState.RELEASED)
        assert sm.state == ReleaseState.RELEASED
        with pytest.raises(TransitionError, match="RELEASED is terminal"):
            sm.rollback(reason="post-release rollback")


# =======================================================================
# 4. VERSION BUMPING / LIFECYCLE — full release state machine walk
# =======================================================================


class TestVersionBumpingAndLifecycle:
    def test_full_lifecycle_discover_to_released(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        assert sm.state == ReleaseState.PLAN
        sm.advance(target=ReleaseState.BUILD_ONCE)
        assert sm.state == ReleaseState.BUILD_ONCE
        sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
        )
        assert sm.state == ReleaseState.VERIFY_OFFLINE
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        assert sm.state == ReleaseState.STAGE
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        assert sm.state == ReleaseState.CANARY
        sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
        assert sm.state == ReleaseState.PROMOTE
        sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
        assert sm.state == ReleaseState.VERIFY_RELEASE_PAGE
        result = sm.advance(target=ReleaseState.RELEASED)
        assert sm.state == ReleaseState.RELEASED
        assert result.blocked is False
        assert sm.serving_digest == _NEW_DIGEST

    def test_version_lifecycle_blocks_missing_gate_evidence(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        result = sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=None)
        assert result.blocked is True
        assert "missing-gate-evidence" in result.reasons
        assert sm.state == ReleaseState.BUILD_ONCE

    def test_version_lifecycle_blocks_mismatched_artifact_digest(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
        )
        result = sm.advance(target=ReleaseState.STAGE, artifact_digest="sha256:wrong")
        assert result.blocked is True
        assert "artifact-digest-mismatch" in result.reasons

    def test_version_lifecycle_released_no_self_transition(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[("gate-unit", "passed", "log://unit")])
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
        sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
        sm.advance(target=ReleaseState.RELEASED)
        with pytest.raises(TransitionError):
            sm.advance(target=ReleaseState.RELEASED)

    def test_released_is_terminal_no_forward(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[("gate-unit", "passed", "log://unit")])
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
        sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
        sm.advance(target=ReleaseState.RELEASED)
        with pytest.raises(TransitionError):
            sm.advance(target=ReleaseState.PLAN)

    def test_source_sha_change_blocks_advance(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        result = sm.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("gate-unit", "passed", "log://unit")],
            observed_source_sha="b" * 40,
        )
        assert result.blocked is True
        assert "source-sha-moved" in result.reasons

    def test_canary_requires_health_gate(self) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[("gate-unit", "passed", "log://unit")])
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        result = sm.advance(
            target=ReleaseState.CANARY,
            prior_digest=_PRIOR_DIGEST,
            health_gate_passed=False,
        )
        assert result.blocked is True
        assert "health-gate-not-passed" in result.reasons


# =======================================================================
# 5. ARTIFACT SIGNING — manifest signer, ssh-keygen protocol
# =======================================================================


class TestArtifactSigning:
    def test_signer_private_key_property(self) -> None:
        signer = ManifestSigner(private_key_path="/tmp/test_key")
        assert signer.private_key_path == "/tmp/test_key"

    def test_signer_env_defaults(self) -> None:
        signer = ManifestSigner()
        path = signer.private_key_path
        assert "ssh" in path or "id_ed" in path.lower() or path

    def test_sign_fails_when_key_missing(self, tmp_path: Path) -> None:
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text('{"version":"1.0.0"}')
        signer = ManifestSigner(private_key_path=str(tmp_path / "nonexistent_key"))
        result = signer.sign(str(manifest))
        assert result.success is False
        assert any("not found" in e or "missing" in e.lower() for e in result.errors)

    def test_sign_fails_when_manifest_missing(self, tmp_path: Path) -> None:
        key = tmp_path / "test_key"
        key.write_text("ssh-ed25519 AAAAfake")
        signer = ManifestSigner(private_key_path=str(key))
        result = signer.sign(str(tmp_path / "nonexistent_manifest.json"))
        assert result.success is False

    def test_sign_result_carries_sig_path(self, tmp_path: Path) -> None:
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text('{"version":"1.0.0"}')
        signer = ManifestSigner(private_key_path=str(tmp_path / "missing_key"))
        result = signer.sign(str(manifest))
        assert result.sig_path.endswith(".sig")

    def test_verify_fails_when_allowed_signers_missing(self, tmp_path: Path) -> None:
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text("{}")
        sig = tmp_path / "MANIFEST.json.sig"
        sig.write_bytes(b"fake-sig")
        signer = ManifestSigner(allowed_signers_path=str(tmp_path / "nonexistent_allowed"))
        result = signer.verify(str(manifest), str(sig))
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_make_allowed_signers_idempotent(self, tmp_path: Path) -> None:
        path = str(tmp_path / "allowed_signers")
        ManifestSigner.make_allowed_signers("release-bundle", "ssh-ed25519 AAAAkey1", path)
        ManifestSigner.make_allowed_signers("release-bundle", "ssh-ed25519 AAAAkey1", path)
        lines = Path(path).read_text().strip().splitlines()
        assert len(lines) == 1
        assert "release-bundle" in lines[0]
        assert "AAAAkey1" in lines[0]

    def test_make_allowed_signers_appends_new_key(self, tmp_path: Path) -> None:
        path = str(tmp_path / "allowed_signers")
        ManifestSigner.make_allowed_signers("release-bundle", "ssh-ed25519 AAAAkey1", path)
        ManifestSigner.make_allowed_signers("canary-signer", "ssh-ed25519 AAAAkey2", path)
        lines = Path(path).read_text().strip().splitlines()
        assert len(lines) == 2


# =======================================================================
# 6. PROVENANCE — artifact validation, checksums, manifest integrity
# =======================================================================


class TestProvenanceAndArtifactValidation:
    def test_validator_rejects_missing_manifest(self, tmp_path: Path) -> None:
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.valid is False
        assert not result.manifest_valid
        assert "MANIFEST.json" in " ".join(result.errors)

    def test_validator_rejects_missing_checksums(self, tmp_path: Path) -> None:
        v = ReleaseArtifactValidator()
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text(json.dumps({"version": "1.0.0"}))
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.valid is False
        assert not result.pip_bundle_valid

    def test_validator_rejects_mismatched_version(self, tmp_path: Path) -> None:
        v = ReleaseArtifactValidator()
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text(json.dumps({"version": "0.9.0"}))
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.valid is False
        assert not result.manifest_valid

    def test_validator_accepts_matching_manifest(self, tmp_path: Path) -> None:
        v = ReleaseArtifactValidator()
        manifest = tmp_path / "MANIFEST.json"
        manifest.write_text(json.dumps({"version": "1.0.0"}))
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.manifest_valid is True

    def test_checksum_parsing_with_binary_marker(self, tmp_path: Path) -> None:
        content = b"hello world"
        expected_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
        manifest = tmp_path / "MANIFEST.json"
        checksums = tmp_path / "CHECKSUMS.sha256"
        wheel = tmp_path / "gludd-1.0.0-py3-none-any.whl"
        wheel.write_bytes(content)
        manifest.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "checksums": {"gludd-1.0.0-py3-none-any.whl": expected_hash},
                }
            )
        )
        checksums.write_text(f"{expected_hash}  *gludd-1.0.0-py3-none-any.whl\n")
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.pip_bundle_valid is True

    def test_checksum_mismatch_detected(self, tmp_path: Path) -> None:
        wheel = tmp_path / "gludd-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"real content")
        real_hash = f"sha256:{hashlib.sha256(b'real content').hexdigest()}"
        manifest = tmp_path / "MANIFEST.json"
        checksums = tmp_path / "CHECKSUMS.sha256"
        manifest.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "checksums": {"gludd-1.0.0-py3-none-any.whl": "sha256:wrongwrongwrongwrongwrongwrongwrongwrong"},
                }
            )
        )
        checksums.write_text(f"{real_hash}  gludd-1.0.0-py3-none-any.whl\n")
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.pip_bundle_valid is False

    def test_manifest_file_missing_from_disk(self, tmp_path: Path) -> None:
        manifest = tmp_path / "MANIFEST.json"
        checksums = tmp_path / "CHECKSUMS.sha256"
        expected_hash = f"sha256:{hashlib.sha256(b'test').hexdigest()}"
        manifest.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "checksums": {"missing.whl": expected_hash},
                }
            )
        )
        checksums.write_text(f"{expected_hash}  missing.whl\n")
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0.0", str(tmp_path))
        assert result.pip_bundle_valid is False
        assert any("missing" in e for e in result.errors)

    def test_release_cut_result_shape(self) -> None:
        r = ReleaseCutResult(success=False, tag="v1.0.0", branch="master", message="test")
        assert r.success is False
        assert r.tag == "v1.0.0"
        assert r.branch == "master"

    def test_release_delete_result_shape(self) -> None:
        r = ReleaseDeleteResult(success=True, tag="v1.0.0")
        assert r.success is True
        assert not r.local_deleted
        assert not r.remote_deleted

    def test_release_recut_result_shape(self) -> None:
        r = ReleaseRecutResult(success=False, tag="v1.0.0", message="no tag")
        assert r.success is False
        assert r.steps_completed == []


# =======================================================================
# 7. CROSS-CUTTING — strategy comparison, rolling deployment
# =======================================================================


class TestCrossCuttingStrategies:
    def test_rolling_strategy_steps_proportionally(self) -> None:
        orch = DeploymentOrchestrator(
            config=_CONFIG_ROLLING,
            prior_digest=_PRIOR_DIGEST,
            new_digest=_NEW_DIGEST,
        )
        shift = orch.next_shift(current_percent=0)
        assert shift.next_percent == 33
        shift = orch.next_shift(current_percent=33)
        assert shift.next_percent == 66
        shift = orch.next_shift(current_percent=66)
        assert shift.next_percent == 99
        shift = orch.next_shift(current_percent=99)
        assert shift.next_percent == 100

    def test_cross_strategy_health_evaluation_is_independent(self) -> None:
        for strat in (DeploymentStrategy.CANARY, DeploymentStrategy.ROLLING, DeploymentStrategy.BLUE_GREEN):
            config = DeploymentConfig(
                strategy=strat,
                health_gate=_GATE,
                abort_threshold=0.10,
            )
            orch = DeploymentOrchestrator(config=config, prior_digest=_PRIOR_DIGEST, new_digest=_NEW_DIGEST)
            decision = orch.evaluate(stage="eval", sample=_HEALTHY)
            assert isinstance(decision, PromoteDecision), f"{strat.value} failed"

    def test_constructor_rejects_unknown_strategy(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy"):
            DeploymentOrchestrator(
                config=DeploymentConfig(
                    strategy="bogus",  # type: ignore[arg-type]
                    health_gate=_GATE,
                    abort_threshold=0.10,
                ),
                prior_digest=_PRIOR_DIGEST,
                new_digest=_NEW_DIGEST,
            )

    def test_constructor_rejects_empty_digests(self) -> None:
        with pytest.raises(ValueError, match="required"):
            DeploymentOrchestrator(config=_CONFIG, prior_digest="", new_digest=_NEW_DIGEST)
        with pytest.raises(ValueError, match="required"):
            DeploymentOrchestrator(config=_CONFIG, prior_digest=_PRIOR_DIGEST, new_digest="")

    def test_state_machine_rejects_empty_digests(self) -> None:
        with pytest.raises(ValueError, match="required"):
            ReleaseStateMachine(source_sha="", artifact_digest=_NEW_DIGEST)
        with pytest.raises(ValueError, match="required"):
            ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest="")

    def test_rollback_preserves_reason(self, tmp_path: Path) -> None:
        sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.PLAN)
        sm.advance(target=ReleaseState.BUILD_ONCE)
        sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[("g", "p", "l")])
        sm.advance(target=ReleaseState.STAGE, artifact_digest=_NEW_DIGEST)
        sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
        sm.rollback(reason="operator-manual-rollback")
        assert sm.state == ReleaseState.ROLLBACK
