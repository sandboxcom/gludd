"""Bounded daemon-side execution facade for language collection roles.

Collection action plugins call this facade through the authenticated daemon.
The algorithms remain authoritative in :mod:`general_ludd.language`; this
module only validates transport payloads and preserves the role result schemas.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import unicodedata
from collections.abc import Callable
from itertools import pairwise

from general_ludd.language.charset_map import (
    ALL_ENCODINGS,
    BOM_OPTIONAL_BY_RFC,
    BOM_REQUIRED_BY_RFC,
    BOM_SIGNATURES,
    BOM_SIZE,
    CHARDET_CONFIDENCE_THRESHOLDS,
    MOJIBAKE_SIGNATURES,
)
from general_ludd.language.detection import detect_language
from general_ludd.language.homoglyph_data import (
    detect_bidi_overrides,
    detect_confusables,
    detect_invisible_chars,
    detect_mixed_script,
)
from general_ludd.language.locale_data import (
    CLDR_FIRST_DAY_OF_WEEK,
    CLDR_MEASUREMENT_SYSTEMS,
    ISO_639_1_TO_NAME,
    RTL_LANGUAGES,
    format_currency,
    format_number,
)
from general_ludd.language.phonetic_data import (
    compute_double_metaphone,
    compute_metaphone,
    compute_soundex,
    transcribe_to_arpabet,
    transcribe_to_ipa,
)
from general_ludd.language.translation import translate
from general_ludd.language.transliteration import transliterate
from general_ludd.language.unicode_data import (
    UNICODE_BLOCK_NAMES,
    UNICODE_CATEGORY_NAMES,
    UNICODE_VERSION_HISTORY,
    is_high_surrogate,
    is_low_surrogate,
    plane_of,
    surrogates_to_codepoint,
)

MAX_PAYLOAD_BYTES = 1_000_000
_SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _validate_payload(payload: dict[str, object]) -> None:
    """Reject non-JSON or unbounded controller requests before computation."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")


def _input_bytes(payload: dict[str, object]) -> bytes | None:
    encoded = payload.get("input_b64")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("input_b64 must be valid base64") from exc
    raw_hex = payload.get("input_bytes")
    if isinstance(raw_hex, str) and raw_hex:
        try:
            return bytes.fromhex(raw_hex)
        except ValueError as exc:
            raise ValueError("input_bytes must be hexadecimal") from exc
    return None


def _hex_preview(data: bytes, maximum: int = 32) -> str:
    rendered = " ".join(f"{byte:02X}" for byte in data[:maximum])
    return rendered + ("..." if len(data) > maximum else "")


def _detect_bom(data: bytes) -> str | None:
    for encoding, signature in sorted(
        BOM_SIGNATURES.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    ):
        if data.startswith(signature):
            return encoding
    return None


def _bom_detect(payload: dict[str, object]) -> dict[str, object]:
    data = _input_bytes(payload)
    if data is None:
        return {"bom_detected": False, "error": "No input provided"}
    detected = _detect_bom(data)
    result: dict[str, object] = {
        "file_size": len(data),
        "bom_detected": detected is not None,
        "encoding": detected,
        "bom_size": 0,
    }
    if detected is not None:
        size = BOM_SIZE.get(detected, len(BOM_SIGNATURES[detected]))
        result.update(
            bom_size=size,
            bom_hex=_hex_preview(data[:size], size),
            rfc_compliance=(
                "required"
                if detected.replace("-", "") in BOM_REQUIRED_BY_RFC
                else "optional"
                if detected in BOM_OPTIONAL_BY_RFC
                else "none"
            ),
        )
        if payload.get("strip_bom") is True:
            result["stripped_preview"] = _hex_preview(data[size:])
    if payload.get("add_bom") is True:
        add_encoding = str(payload.get("add_bom_encoding", "UTF-8"))
        signature = BOM_SIGNATURES.get(add_encoding)
        if signature is None:
            result["add_bom_error"] = f"Unknown BOM encoding: {add_encoding}"
        else:
            modified = data if data.startswith(signature) else signature + data
            result["bom_added"] = add_encoding
            result["with_bom_hex"] = _hex_preview(modified, 64)
    audit_files = payload.get("audit_files")
    if isinstance(audit_files, dict):
        audit: list[dict[str, object]] = []
        for path, content in sorted(audit_files.items(), key=lambda item: str(item[0])):
            if not isinstance(content, str):
                continue
            try:
                head = base64.b64decode(content, validate=True)[:4]
            except ValueError:
                continue
            audit.append({"file": str(path), "bom": _detect_bom(head)})
        result["audit_results"] = audit
    return result


def _encoding_detect(payload: dict[str, object]) -> dict[str, object]:
    data = _input_bytes(payload)
    if data is None:
        return {"error": "No input provided"}
    try:
        import chardet

        detected = chardet.detect(data)
        encoding = str(detected.get("encoding") or "unknown")
        confidence = round(float(detected.get("confidence") or 0.0), 4)
    except ImportError:
        encoding, confidence = "utf-8", 0.5
    thresholds = CHARDET_CONFIDENCE_THRESHOLDS
    level = next(
        (
            name
            for name in ("trusted", "reliable", "usable")
            if confidence >= thresholds.get(name, 1.0)
        ),
        "entry",
    )
    result: dict[str, object] = {
        "byte_length": len(data),
        "detected_encoding": encoding,
        "confidence": confidence,
        "confidence_level": level,
        "supported_encodings": [item["name"] for item in ALL_ENCODINGS],
        "supported_count": len(ALL_ENCODINGS),
    }
    try:
        decoded = data.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        decoded = data.decode("utf-8", errors="replace")
        result["decode_error"] = True
    result["char_length"] = len(decoded)
    result["converted_preview"] = decoded[:200]
    target = payload.get("target_encoding")
    if isinstance(target, str) and target:
        try:
            result["target_byte_length"] = len(decoded.encode(target))
        except (LookupError, UnicodeEncodeError):
            result["target_encoding_error"] = True
    if payload.get("detect_mojibake") is True:
        found = next(
            (
                name
                for name, patterns in MOJIBAKE_SIGNATURES.items()
                if any(pattern and pattern in decoded for pattern in patterns)
            ),
            None,
        )
        result["mojibake_detected"] = found is not None
        result["mojibake_pattern"] = found
    return result


def _homoglyph_scan(payload: dict[str, object]) -> dict[str, object]:
    text = str(payload.get("input_text") or payload.get("input_domain") or "")
    if not text:
        return {
            "input_length": 0,
            "findings": [],
            "attack_vectors": [],
            "total_findings": 0,
            "severity_counts": {},
            "safe": True,
        }
    findings: list[dict[str, object]] = []
    if payload.get("check_confusables", True):
        findings.extend({**item, "type": "confusable", "severity": "medium"} for item in detect_confusables(text))
    if payload.get("check_invisible", True):
        findings.extend({**item, "type": "invisible", "severity": "high"} for item in detect_invisible_chars(text))
    if payload.get("check_bidi", True):
        findings.extend({**item, "type": "bidi_spoof", "severity": "critical"} for item in detect_bidi_overrides(text))
    mixed = detect_mixed_script(text) if payload.get("check_mixed_script", True) else None
    if mixed and mixed.get("mixed"):
        findings.append({**mixed, "type": "mixed_script", "severity": "medium"})
    minimum = _SEVERITY.get(str(payload.get("min_severity", "low")), 0)
    findings = [item for item in findings if _SEVERITY.get(str(item["severity"]), 0) >= minimum]
    severity_counts = {
        severity: sum(item["severity"] == severity for item in findings)
        for severity in _SEVERITY
        if any(item["severity"] == severity for item in findings)
    }
    return {
        "input_length": len(text),
        "findings": findings,
        "attack_vectors": [],
        "total_findings": len(findings),
        "severity_counts": severity_counts,
        "safe": not findings,
        "scripts_detected": mixed.get("scripts", []) if mixed else [],
    }


def _locale_format(payload: dict[str, object]) -> dict[str, object]:
    locale = str(payload.get("locale", "en-US"))
    base_locale = locale.split(".", 1)[0].replace("_", "-")
    parts = base_locale.split("-")
    language = parts[0].lower() if parts else ""
    territory = parts[1].upper() if len(parts) > 1 else ""
    value = str(payload.get("value", ""))
    value_type = str(payload.get("value_type", "date"))
    try:
        if value_type == "number":
            formatted = format_number(float(value), base_locale)
        elif value_type == "currency":
            formatted = format_currency(
                float(value),
                str(payload.get("currency_code", "USD")),
                base_locale,
            )
        elif value_type == "date":
            formatted = dt.date.fromisoformat(value).isoformat()
        else:
            formatted = value
    except (ValueError, TypeError):
        formatted = value
    result: dict[str, object] = {
        "locale": locale,
        "language": language,
        "language_name": ISO_639_1_TO_NAME.get(language, "Unknown"),
        "territory": territory,
        "is_rtl": language in RTL_LANGUAGES,
        "formatted_value": formatted,
        "first_day_of_week": CLDR_FIRST_DAY_OF_WEEK.get(territory, 1),
        "measurement_system": CLDR_MEASUREMENT_SYSTEMS.get(territory, "metric"),
    }
    if "." in locale:
        result["codeset"] = locale.rsplit(".", 1)[1]
    return result


def _phonetic_transcribe(payload: dict[str, object]) -> dict[str, object]:
    text = str(payload.get("input_text", ""))
    method = str(payload.get("method", "arpabet"))
    words = text.split()
    functions: dict[str, Callable[[str], object]] = {
        "arpabet": transcribe_to_arpabet,
        "ipa": transcribe_to_ipa,
        "soundex": compute_soundex,
        "metaphone": compute_metaphone,
        "double_metaphone": compute_double_metaphone,
    }
    if method not in functions:
        raise ValueError(f"unsupported phonetic method: {method}")
    entries: list[dict[str, object]] = []
    for word in words:
        value = functions[method](word)
        transcription = " / ".join(value) if isinstance(value, tuple) else str(value)
        entries.append({"word": word.upper(), "transcription": transcription})
    ipa = transcribe_to_ipa(text) if text.strip() else ""
    return {"input_text": text, "method": method, "words": entries, "ipa": ipa}


def _unicode_analyze(payload: dict[str, object]) -> dict[str, object]:
    direct_text = payload.get("input_text", "")
    text = str(direct_text) if direct_text else ""
    if not text and payload.get("input_b64"):
        data = _input_bytes(payload)
        try:
            text = data.decode("utf-8") if data is not None else ""
        except UnicodeDecodeError as exc:
            raise ValueError("unicode input file must contain valid UTF-8") from exc
    result: dict[str, object] = {
        "input_length": len(text),
        "input_byte_length": len(text.encode("utf-8", errors="surrogatepass")),
    }
    if payload.get("include_codepoints", True):
        result["codepoints"] = [
            {
                "index": index,
                "char": char,
                "codepoint": f"U+{ord(char):04X}",
                "category": unicodedata.category(char),
                "category_name": UNICODE_CATEGORY_NAMES.get(unicodedata.category(char), ""),
                "block": next(
                    (
                        name
                        for (lower, upper), name in UNICODE_BLOCK_NAMES.items()
                        if lower <= ord(char) <= upper
                    ),
                    "Unknown",
                ),
                "plane": plane_of(ord(char)),
                "name": unicodedata.name(char, ""),
            }
            for index, char in enumerate(text)
        ]
    if payload.get("include_normalization", True):
        result["normalization"] = {
            "NFC": unicodedata.normalize("NFC", text),
            "NFD": unicodedata.normalize("NFD", text),
            "NFKC": unicodedata.normalize("NFKC", text),
            "NFKD": unicodedata.normalize("NFKD", text),
        }
    if payload.get("include_grapheme_clusters", True):
        result["grapheme_clusters"] = [
            {"index": index, "text": char} for index, char in enumerate(text)
        ]
    if payload.get("include_surrogates", True):
        codepoints = [ord(char) for char in text]
        surrogates: list[dict[str, object]] = []
        for index, (high, low) in enumerate(pairwise(codepoints)):
            if is_high_surrogate(high) and is_low_surrogate(low):
                surrogates.append(
                    {
                        "index": index,
                        "high": f"U+{high:04X}",
                        "low": f"U+{low:04X}",
                        "decoded": f"U+{surrogates_to_codepoint(high, low):04X}",
                    }
                )
        result["surrogates"] = surrogates
    if payload.get("include_plane_distribution", True):
        distribution: dict[str, int] = {}
        for char in text:
            plane = plane_of(ord(char))
            distribution[plane] = distribution.get(plane, 0) + 1
        result["plane_distribution"] = distribution
    if payload.get("include_utf_encodings", True):
        result["utf_encodings"] = {
            "UTF-8": text.encode("utf-8", errors="surrogatepass").hex(" "),
            "UTF-16-LE": text.encode("utf-16-le", errors="surrogatepass").hex(" "),
            "UTF-16-BE": text.encode("utf-16-be", errors="surrogatepass").hex(" "),
            "UTF-32-LE": text.encode("utf-32-le", errors="surrogatepass").hex(" "),
            "UTF-32-BE": text.encode("utf-32-be", errors="surrogatepass").hex(" "),
        }
    if payload.get("include_version_info", False):
        result["unicode_versions"] = UNICODE_VERSION_HISTORY
    return result


def execute_language_operation(
    operation: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Execute one allowlisted language operation using core-owned algorithms."""
    _validate_payload(payload)
    direct: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
        "bom_detect": _bom_detect,
        "encoding_detect": _encoding_detect,
        "homoglyph_scan": _homoglyph_scan,
        "locale_format": _locale_format,
        "phonetic_transcribe": _phonetic_transcribe,
        "unicode_analyze": _unicode_analyze,
    }
    if operation in direct:
        return direct[operation](payload)
    if operation == "language_detect":
        return dict(detect_language(str(payload.get("input_text", ""))))
    if operation == "translate":
        return dict(
            translate(
                str(payload.get("input_text", "")),
                str(payload.get("source_language", "auto")),
                str(payload.get("target_language", "en")),
                allow_network=False,
            )
        )
    if operation == "transliterate":
        scheme = payload.get("scheme")
        return dict(
            transliterate(
                str(payload.get("input_text", "")),
                str(payload.get("target_script", "Latin")),
                str(scheme) if scheme else None,
            )
        )
    raise ValueError(f"unsupported language operation: {operation}")


__all__ = ["MAX_PAYLOAD_BYTES", "execute_language_operation"]
