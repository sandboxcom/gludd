"""Unit tests for AIML Phase D: speech recognition (AIML-010) and synthesis (AIML-011).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §7.1 (ASR) and §7.2 (TTS):

  - ASR contract accepts audio artifact URI, language hint, streaming flag,
    speaker-count bounds, timestamp granularity, vocabulary hints, and privacy
    class; returns transcript segments with start/end, speaker label, language,
    confidence, and non-speech events (AIML-AT-010).
  - Streaming results expose partial vs final status and monotonically
    increasing sequence numbers.
  - Audio retention defaults to zero after finalization unless the caller
    explicitly requests a permitted artifact (spec §7.1).
  - TTS refuses unconsented custom voices; consent requires identity evidence,
    use scope, expiry, and audit (AIML-AT-011, spec §7.2).
  - TTS outputs include audio digest, text digest, voice/model versions,
    synthesis parameters, consent reference, and provenance marking.
  - WER (word error rate) calculation stub for ASR evaluation (spec §7.1).
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.speech import (
    ASRRequest,
    ASRResult,
    ASRSegment,
    ConsentDecision,
    TTSRequest,
    TTSResult,
    VoiceConsent,
    check_voice_consent,
    compute_audio_retention,
    is_custom_voice,
    word_error_rate,
)

_SHA_IDENTITY = "a" * 64
_SHA_AUDIO = "b" * 64
_SHA_TEXT = "c" * 64
_SHA_AUDIO_OUT = "d" * 64


# ---------------------------------------------------------------------------
# AIML-010 — ASR contract (spec §7.1, AIML-AT-010)
# ---------------------------------------------------------------------------


class TestASRContract:
    def test_request_carries_artifact_uri_language_streaming_flag(self) -> None:
        """Spec §7.1: ASR accepts audio artifact URI, language hint, streaming flag."""
        req = ASRRequest(
            audio_artifact_uri="artifact://audio/abc.wav",
            language_hint="en-US",
            streaming=True,
        )
        assert req.audio_artifact_uri == "artifact://audio/abc.wav"
        assert req.language_hint == "en-US"
        assert req.streaming is True

    def test_result_segments_carry_start_end_speaker_language_confidence(self) -> None:
        """Spec §7.1: segments carry start/end time, speaker, language, confidence."""
        seg = ASRSegment(
            text="hello world",
            start_s=0.0,
            end_s=1.25,
            speaker_label="spk-1",
            language="en-US",
            confidence=0.92,
        )
        result = ASRResult(
            request_id="req-1",
            segments=(seg,),
            language_id="en-US",
            is_final=True,
            sequence_number=1,
        )
        assert result.segments[0].start_s == 0.0
        assert result.segments[0].end_s == 1.25
        assert result.segments[0].speaker_label == "spk-1"
        assert result.segments[0].language == "en-US"
        assert 0.0 <= result.segments[0].confidence <= 1.0

    def test_streaming_partial_vs_final_and_monotonic_sequence(self) -> None:
        """Spec §7.1: streaming results expose partial vs final + monotonic seq."""
        partial = ASRResult(
            request_id="req-1",
            segments=(ASRSegment("hello", 0.0, 0.5, None, "en-US", 0.6),),
            language_id="en-US",
            is_final=False,
            sequence_number=1,
        )
        final = ASRResult(
            request_id="req-1",
            segments=(ASRSegment("hello world", 0.0, 1.0, None, "en-US", 0.9),),
            language_id="en-US",
            is_final=True,
            sequence_number=2,
        )
        assert partial.is_final is False
        assert final.is_final is True
        assert final.sequence_number > partial.sequence_number

    def test_segment_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ASRSegment("x", 0.0, 1.0, None, "en", 1.5)

    def test_segment_rejects_end_before_start(self) -> None:
        with pytest.raises(ValueError, match="end_s"):
            ASRSegment("x", 2.0, 1.0, None, "en", 0.5)


class TestAudioRetention:
    def test_audio_retention_defaults_to_zero(self) -> None:
        """Spec §7.1: audio retention defaults to zero after finalization."""
        req = ASRRequest(
            audio_artifact_uri="artifact://audio/abc.wav",
            language_hint=None,
            streaming=False,
        )
        assert req.audio_retention_seconds == 0

    def test_audio_retained_only_when_permitted_artifact_requested(self) -> None:
        """Spec §7.1: zero retention unless caller explicitly requests a permitted artifact."""
        req_default = ASRRequest(
            audio_artifact_uri="artifact://audio/abc.wav",
            language_hint=None,
            streaming=False,
        )
        # Default: zero retention, no artifact requested -> not retained.
        retained_default, _ = compute_audio_retention(req_default, artifact_requested=False)
        assert retained_default is False
        # Retention > 0 but no artifact requested -> still not retained.
        req_retention = ASRRequest(
            audio_artifact_uri="artifact://audio/abc.wav",
            language_hint=None,
            streaming=False,
            audio_retention_seconds=3600,
        )
        retained_no_req, _ = compute_audio_retention(req_retention, artifact_requested=False)
        assert retained_no_req is False
        # Retention > 0 AND artifact requested -> retained.
        retained_yes, _ = compute_audio_retention(req_retention, artifact_requested=True)
        assert retained_yes is True


# ---------------------------------------------------------------------------
# AIML-011 — TTS consent gate (spec §7.2, AIML-AT-011)
# ---------------------------------------------------------------------------


class TestTTSConsent:
    def test_is_custom_voice_detects_custom_prefix(self) -> None:
        assert is_custom_voice("custom:voice-aria") is True
        assert is_custom_voice("stock:en-US-Aria") is False

    def test_check_voice_consent_refuses_unconsented_custom_voice(self) -> None:
        """Spec §7.2: requests that imitate a real person without verified permission are refused."""
        decision = check_voice_consent(
            voice_id="custom:voice-aria",
            consent=None,
            now=1_700_000_000,
        )
        assert isinstance(decision, ConsentDecision)
        assert decision.allowed is False
        assert "consent" in decision.reason.lower()

    def test_consent_requires_identity_evidence_use_scope_expiry_audit(self) -> None:
        """Spec §7.2: a custom voice requires identity/consent evidence, use scope, expiry, audit."""
        consent = VoiceConsent(
            voice_id="custom:voice-aria",
            identity_evidence_sha256=_SHA_IDENTITY,
            use_scope="narration-demo-v1",
            expires_at=1_800_000_000,
            audit_id="audit-001",
        )
        decision = check_voice_consent(
            voice_id="custom:voice-aria",
            consent=consent,
            now=1_700_000_000,
        )
        assert decision.allowed is True
        assert decision.audit_id == "audit-001"

    def test_expired_consent_is_refused(self) -> None:
        consent = VoiceConsent(
            voice_id="custom:voice-aria",
            identity_evidence_sha256=_SHA_IDENTITY,
            use_scope="narration",
            expires_at=1_600_000_000,
            audit_id="audit-002",
        )
        decision = check_voice_consent(
            voice_id="custom:voice-aria",
            consent=consent,
            now=1_700_000_000,
        )
        assert decision.allowed is False
        assert "expired" in decision.reason.lower()

    def test_consent_missing_identity_evidence_refused(self) -> None:
        """A consent record with invalid identity evidence cannot even be
        constructed — the sha256 is validated structurally, so a malformed
        consent can never reach the check (defensive by construction)."""
        with pytest.raises(ValueError, match="identity_evidence_sha256") as exc_info:
            VoiceConsent(
                voice_id="custom:voice-aria",
                identity_evidence_sha256="not-a-sha",
                use_scope="narration",
                expires_at=1_800_000_000,
                audit_id="audit-003",
            )
        assert "identity_evidence_sha256" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AIML-011 — TTS provenance (spec §7.2)
# ---------------------------------------------------------------------------


class TestTTSProvenance:
    def test_tts_result_emits_provenance_metadata(self) -> None:
        """Spec §7.2: outputs include audio digest, text digest, voice/model
        versions, synthesis parameters, consent reference, provenance marking."""
        result = TTSResult(
            audio_artifact_uri="artifact://tts/out.wav",
            audio_digest_sha256=_SHA_AUDIO_OUT,
            text_digest_sha256=_SHA_TEXT,
            voice_id="stock:en-US-Aria",
            voice_model_version="tts-2024-06-01",
            synthesis_params=("pace=1.0", "pitch=0", "format=wav", "sample_rate=22050"),
            consent_reference=None,
            provenance=("synthetic", "watermarked", "voice-id=stock:en-US-Aria"),
        )
        assert result.audio_digest_sha256 == _SHA_AUDIO_OUT
        assert result.text_digest_sha256 == _SHA_TEXT
        assert result.voice_model_version == "tts-2024-06-01"
        assert result.consent_reference is None
        assert any("synthetic" in p for p in result.provenance)

    def test_tts_request_validates_text_and_voice(self) -> None:
        req = TTSRequest(
            text_or_ssml="Hello world",
            language="en-US",
            voice_id="stock:en-US-Aria",
        )
        assert req.voice_id == "stock:en-US-Aria"
        assert req.audio_format == "wav"
        assert req.streaming is False
        with pytest.raises(ValueError, match="text_or_ssml"):
            TTSRequest(text_or_ssml="", language="en", voice_id="stock:x")


# ---------------------------------------------------------------------------
# WER calculation stub (spec §7.1 evaluation)
# ---------------------------------------------------------------------------


class TestWordErrorRate:
    def test_identical_strings_have_zero_wer(self) -> None:
        assert word_error_rate("the quick brown fox", "the quick brown fox") == 0.0

    def test_totally_different_strings_have_high_wer(self) -> None:
        wer = word_error_rate("alpha bravo charlie", "zulu yankee xray")
        assert 0.0 <= wer <= 1.0
        assert wer > 0.5

    def test_empty_reference_and_hypothesis_is_zero(self) -> None:
        assert word_error_rate("", "") == 0.0
