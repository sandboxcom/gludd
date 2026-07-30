"""AIML Phase D — speech recognition (AIML-010) and synthesis (AIML-011).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §7.1 (ASR) and §7.2 (TTS):

  - :class:`ASRRequest` / :class:`ASRSegment` / :class:`ASRResult` — the ASR
    contract: audio artifact URI, language hint, streaming flag, speaker-count
    bounds, timestamp granularity, vocabulary hints, privacy class. Returns
    normalized transcript segments with start/end time, speaker label, language,
    confidence, and non-speech events (spec §7.1). Streaming results expose
    partial vs final status and monotonically increasing sequence numbers.
  - :func:`compute_audio_retention` — spec §7.1: "Audio retention defaults to
    zero after result finalization unless the caller explicitly requests a
    permitted artifact."
  - :class:`VoiceConsent` / :class:`ConsentDecision` / :func:`check_voice_consent`
    — spec §7.2 consent gate: "A custom voice requires identity/consent
    evidence, intended-use scope, expiry, and audit. Requests that imitate a
    real person without verified permission are refused."
  - :class:`TTSRequest` / :class:`TTSResult` — the TTS contract carrying audio
    digest, text digest, voice/model versions, synthesis parameters, consent
    reference, and provenance marking (spec §7.2).
  - :func:`word_error_rate` — WER calculation stub for ASR evaluation (spec §7.1
    "Evaluation includes word/character error rate ...").

This module holds the typed contract; the ``speech_recognize`` and
``speech_synthesize`` ansible roles wrap these entry points and never shell out.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TimestampGranularity(enum.StrEnum):
    """ASR timestamp granularity (spec §7.1)."""

    WORD = "word"
    SEGMENT = "segment"
    FRAME = "frame"


class PrivacyClass(enum.StrEnum):
    """ASR privacy class (spec §7.1 ``privacy class``).

    Reuses the :class:`~general_ludd.ai_ml.schemas.DataClassification` values
    under speech-specific naming for readability at call sites.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class NonSpeechEvent(enum.StrEnum):
    """Common non-speech events surfaced on ASR segments (spec §7.1)."""

    SILENCE = "silence"
    NOISE = "noise"
    MUSIC = "music"
    LAUGHTER = "laughter"
    FILLER = "filler"


# ---------------------------------------------------------------------------
# ASR request / segment / result (spec §7.1, AIML-AT-010)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ASRRequest:
    """ASR contract input (spec §7.1).

    Accepts audio artifact URI, language hint, streaming flag, speaker count
    bounds, timestamp granularity, vocabulary hints, and privacy class. Audio
    retention defaults to zero (spec §7.1).
    """

    audio_artifact_uri: str
    streaming: bool = False
    language_hint: str | None = None
    speaker_count_bounds: tuple[int, int] | None = None
    timestamp_granularity: TimestampGranularity = TimestampGranularity.SEGMENT
    vocabulary_hints: tuple[str, ...] = ()
    privacy_class: PrivacyClass = PrivacyClass.PUBLIC
    audio_retention_seconds: int = 0
    request_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.audio_artifact_uri, "audio_artifact_uri")
        object.__setattr__(
            self,
            "timestamp_granularity",
            _coerce_enum(self.timestamp_granularity, TimestampGranularity, "timestamp_granularity"),
        )
        object.__setattr__(
            self,
            "privacy_class",
            _coerce_enum(self.privacy_class, PrivacyClass, "privacy_class"),
        )
        if self.audio_retention_seconds < 0:
            raise ValueError(f"audio_retention_seconds must be >= 0, got {self.audio_retention_seconds}")
        if self.speaker_count_bounds is not None:
            lo, hi = self.speaker_count_bounds
            if lo < 0 or hi < lo:
                raise ValueError(f"speaker_count_bounds must satisfy 0 <= min <= max, got {self.speaker_count_bounds}")


@dataclass(frozen=True)
class ASRSegment:
    """One normalized transcript segment (spec §7.1).

    Carries start/end time, speaker label, language, confidence, and
    non-speech events.
    """

    text: str
    start_s: float
    end_s: float
    speaker_label: str | None
    language: str
    confidence: float
    non_speech_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if not isinstance(self.start_s, (int, float)) or self.start_s < 0:
            raise ValueError(f"start_s must be >= 0, got {self.start_s}")
        if not isinstance(self.end_s, (int, float)) or self.end_s < self.start_s:
            raise ValueError(f"end_s must be >= start_s ({self.start_s}), got {self.end_s}")
        _require_nonempty_str(self.language, "language")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.speaker_label is not None and not self.speaker_label.strip():
            raise ValueError("speaker_label, when set, must be a non-empty string")


@dataclass(frozen=True)
class ASRResult:
    """ASR contract output (spec §7.1).

    Streaming results expose partial vs final status (``is_final``) and
    monotonically increasing sequence numbers (``sequence_number``).
    """

    request_id: str
    segments: tuple[ASRSegment, ...]
    language_id: str
    is_final: bool
    sequence_number: int
    audio_retained: bool = False
    retained_artifact_uri: str | None = None

    def __post_init__(self) -> None:
        if self.request_id and not self.request_id.strip():
            raise ValueError("request_id, when set, must be non-empty")
        _require_nonempty_str(self.language_id, "language_id")
        if self.sequence_number < 0:
            raise ValueError(f"sequence_number must be >= 0 (monotonic), got {self.sequence_number}")
        if self.retained_artifact_uri is not None and not self.retained_artifact_uri.strip():
            raise ValueError("retained_artifact_uri, when set, must be non-empty")


def compute_audio_retention(request: ASRRequest, *, artifact_requested: bool) -> tuple[bool, str]:
    """Apply the spec §7.1 audio retention default.

    Spec: "Audio retention defaults to zero after result finalization unless
    the caller explicitly requests a permitted artifact." Returns ``(retained,
    reason)``. Retention requires BOTH a non-zero retention window AND an
    explicit artifact request.
    """
    if request.audio_retention_seconds <= 0:
        return False, "audio retention default is zero"
    if not artifact_requested:
        return False, "caller did not explicitly request a permitted artifact"
    return True, f"retained for {request.audio_retention_seconds}s per explicit request"


# ---------------------------------------------------------------------------
# TTS consent gate (spec §7.2, AIML-AT-011)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceConsent:
    """Verified consent for a custom voice (spec §7.2).

    A custom voice requires identity/consent evidence, intended-use scope,
    expiry, and audit. The ``identity_evidence_sha256`` is a content-addressed
    digest of the collected identity evidence (not the evidence itself).
    """

    voice_id: str
    identity_evidence_sha256: str
    use_scope: str
    expires_at: int
    audit_id: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.voice_id, "voice_id")
        _require_sha256(self.identity_evidence_sha256, "identity_evidence_sha256")
        _require_nonempty_str(self.use_scope, "use_scope")
        _require_nonempty_str(self.audit_id, "audit_id")


@dataclass(frozen=True)
class ConsentDecision:
    """The typed verdict from :func:`check_voice_consent`.

    ``allowed`` is True only when consent was verified for the requested voice.
    ``audit_id`` is always present so a refusal is auditable even when no
    consent record was supplied.
    """

    allowed: bool
    reason: str
    audit_id: str
    voice_id: str


def is_custom_voice(voice_id: str) -> bool:
    """Return True for custom (clonable) voice IDs (spec §7.2 anti-impersonation).

    Stock voices (``stock:<name>``) are publisher-provided and do not require
    per-subject consent. Any voice under the ``custom:`` namespace imitates a
    specific person and requires verified consent.
    """
    _require_nonempty_str(voice_id, "voice_id")
    return voice_id.startswith("custom:")


def check_voice_consent(*, voice_id: str, consent: VoiceConsent | None, now: int) -> ConsentDecision:
    """Verify consent for a TTS voice (spec §7.2, AIML-AT-011).

    Spec: "A custom voice requires identity/consent evidence, intended-use
    scope, expiry, and audit. Requests that imitate a real person without
    verified permission are refused."
    """
    _require_nonempty_str(voice_id, "voice_id")

    if not is_custom_voice(voice_id):
        # Stock voices are not impersonations; consent optional but honored if given.
        audit = consent.audit_id if consent is not None else "stock-no-audit"
        return ConsentDecision(
            allowed=True,
            reason="stock voice; no per-subject consent required",
            audit_id=audit,
            voice_id=voice_id,
        )

    # Custom voice — consent mandatory.
    if consent is None:
        return ConsentDecision(
            allowed=False,
            reason="custom voice requires verified consent; none supplied",
            audit_id="missing",
            voice_id=voice_id,
        )

    # Identity evidence already validated by VoiceConsent.__post_init__; the
    # consent must also target the SAME voice id.
    if consent.voice_id != voice_id:
        return ConsentDecision(
            allowed=False,
            reason="consent voice_id mismatch",
            audit_id=consent.audit_id,
            voice_id=voice_id,
        )

    # Use scope is required (validated at construction); expiry enforced here.
    if consent.expires_at <= now:
        return ConsentDecision(
            allowed=False,
            reason=f"consent expired at {consent.expires_at} (now={now})",
            audit_id=consent.audit_id,
            voice_id=voice_id,
        )

    return ConsentDecision(
        allowed=True,
        reason="consent verified (identity evidence + use scope + expiry + audit)",
        audit_id=consent.audit_id,
        voice_id=voice_id,
    )


# ---------------------------------------------------------------------------
# TTS request / result (spec §7.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TTSRequest:
    """TTS contract input (spec §7.2).

    Accepts text/SSML, language, approved voice ID, pronunciation lexicon,
    pace, pitch, format, sample rate, streaming flag, and optional consent
    (required for custom voices — enforce via :func:`check_voice_consent`).
    """

    text_or_ssml: str
    language: str
    voice_id: str
    pronunciation_lexicon_uri: str | None = None
    pace: float = 1.0
    pitch: float = 0.0
    audio_format: str = "wav"
    sample_rate_hz: int = 22050
    streaming: bool = False
    consent: VoiceConsent | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.text_or_ssml, "text_or_ssml")
        _require_nonempty_str(self.language, "language")
        _require_nonempty_str(self.voice_id, "voice_id")
        if self.pace <= 0:
            raise ValueError(f"pace must be > 0, got {self.pace}")
        if self.sample_rate_hz <= 0:
            raise ValueError(f"sample_rate_hz must be > 0, got {self.sample_rate_hz}")
        if self.pronunciation_lexicon_uri is not None and not self.pronunciation_lexicon_uri.strip():
            raise ValueError("pronunciation_lexicon_uri, when set, must be non-empty")


@dataclass(frozen=True)
class TTSResult:
    """TTS contract output (spec §7.2).

    Includes audio digest, text digest, voice/model versions, synthesis
    parameters, consent reference when applicable, and supported provenance
    marking.
    """

    audio_artifact_uri: str
    audio_digest_sha256: str
    text_digest_sha256: str
    voice_id: str
    voice_model_version: str
    synthesis_params: tuple[str, ...]
    provenance: tuple[str, ...]
    consent_reference: str | None = None
    adapter_version: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.audio_artifact_uri, "audio_artifact_uri")
        _require_sha256(self.audio_digest_sha256, "audio_digest_sha256")
        _require_sha256(self.text_digest_sha256, "text_digest_sha256")
        _require_nonempty_str(self.voice_id, "voice_id")
        _require_nonempty_str(self.voice_model_version, "voice_model_version")


# ---------------------------------------------------------------------------
# WER calculation stub (spec §7.1 evaluation)
# ---------------------------------------------------------------------------


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word error rate between ``reference`` and ``hypothesis`` (spec §7.1).

    Standard WER: (substitutions + insertions + deletions) / reference-word-count,
    computed via word-level Levenshtein distance. Returns ``0.0`` when both
    strings are empty. When the reference is empty but the hypothesis is not,
    returns ``1.0`` (every hypothesis word is an insertion). The result is
    clamped to ``[0.0, 1.0]``.
    """
    ref = reference.split()
    hyp = hypothesis.split()

    if not ref:
        return 0.0 if not hyp else 1.0

    n = len(ref)
    m = len(hyp)

    # dp[i][j] = edit distance between ref[:i] and hyp[:j]
    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i  # i deletions
    for j in range(m + 1):
        dp[0][j] = j  # j insertions

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j],  # deletion
                    dp[i][j - 1],  # insertion
                    dp[i - 1][j - 1],  # substitution
                )

    distance = dp[n][m]
    wer = distance / n
    return max(0.0, min(1.0, wer))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_enum(value: object, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    """Coerce a string or enum member; raise ValueError on miss."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


__all__ = [
    "ASRRequest",
    "ASRResult",
    "ASRSegment",
    "ConsentDecision",
    "NonSpeechEvent",
    "PrivacyClass",
    "TTSRequest",
    "TTSResult",
    "TimestampGranularity",
    "VoiceConsent",
    "check_voice_consent",
    "compute_audio_retention",
    "is_custom_voice",
    "word_error_rate",
]
