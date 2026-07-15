"""``gludd language`` CLI subcommand — Language Expert operations.

Provides four subcommands:
  detect-encoding     Detect character encoding of a file
  scan-homoglyphs     Scan text for confusable/homoglyph characters
  detect-bom          Detect and handle Byte Order Marks
  phonetic-transcribe Convert text to phonetic representations

Each handler calls into the corresponding knowledge module under
``general_ludd.language.*`` and prints a JSON artifact.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def _cmd_language_detect_encoding(args: argparse.Namespace) -> None:
    from general_ludd.language.charset_map import (
        ALL_ENCODINGS,
        CHARDET_CONFIDENCE_THRESHOLDS,
        MOJIBAKE_SIGNATURES,
    )

    if not args.file:
        print("Error: detect-encoding requires a FILE argument", file=sys.stderr)
        sys.exit(1)

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    result: dict[str, object] = {"file": filepath}
    data: bytes | None = None

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except OSError as exc:
        result["error"] = str(exc)
        print(json.dumps(result, indent=2))
        return

    result["file_size"] = len(data)

    detected_encoding = "unknown"
    confidence = 0.0
    try:
        import chardet
        detection = chardet.detect(data)
        detected_encoding = detection.get("encoding", "unknown") or "unknown"
        confidence = detection.get("confidence", 0.0) or 0.0
    except ImportError:
        detected_encoding = "unknown"
        confidence = 0.0

    result["detected_encoding"] = detected_encoding
    result["confidence"] = confidence

    def _confidence_level(conf: float) -> str:
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("trusted", 0.95):
            return "trusted"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("reliable", 0.80):
            return "reliable"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("usable", 0.50):
            return "usable"
        return "entry"

    result["confidence_level"] = _confidence_level(confidence)
    result["converted_preview"] = ""
    try:
        preview = data[:200].decode(detected_encoding if detected_encoding != "unknown" else "utf-8", errors="replace")
        result["converted_preview"] = preview
    except (LookupError, UnicodeDecodeError):
        pass

    if getattr(args, "detect_mojibake", False):
        try:
            text = data.decode("utf-8", errors="replace")
            found_pat: str | None = None
            for sig_name, sig_patterns_array in MOJIBAKE_SIGNATURES.items():
                if isinstance(sig_patterns_array, list):
                    for pat_str in sig_patterns_array:
                        if isinstance(pat_str, str) and pat_str in text:
                            found_pat = sig_name
                            break
                    if found_pat:
                        break
            result["mojibake_detected"] = found_pat is not None
            result["mojibake_pattern"] = found_pat
        except (UnicodeDecodeError, LookupError):
            result["mojibake_detected"] = True
            result["mojibake_pattern"] = "decode_error"

    if getattr(args, "list_encodings", False):
        result["supported_encodings"] = [e["name"] for e in ALL_ENCODINGS]

    print(json.dumps(result, indent=2))


def _cmd_language_scan_homoglyphs(args: argparse.Namespace) -> None:
    from general_ludd.language.homoglyph_data import (
        ATTACK_VECTORS,
        HOMOGLYPH_GROUPS,
        INVISIBLE_CHARACTERS,
    )

    text = args.text or ""
    if not text:
        result: dict[str, object] = {
            "input_length": 0,
            "findings": [],
            "attack_vectors": [],
            "safe": True,
            "total_findings": 0,
            "severity_counts": {},
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    result = {"input_length": len(text)}
    min_sev_str: str = getattr(args, "min_severity", "low") or "low"
    SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    min_sev = SEVERITY_RANK.get(min_sev_str, 0)

    cp_to_skeleton: dict[int, str] = {}
    for group in HOMOGLYPH_GROUPS:
        for cp, _name in group["characters"]:
            cp_to_skeleton[cp] = group["skeleton"]

    invisible_set: set[int] = {inv["codepoint"] for inv in INVISIBLE_CHARACTERS}
    invisible_map: dict[int, dict[str, object]] = {
        inv["codepoint"]: dict(inv) for inv in INVISIBLE_CHARACTERS
    }

    findings: list[dict[str, object]] = []
    for idx, ch in enumerate(text):
        cp = ord(ch)
        hex_cp = f"U+{cp:04X}"

        if cp in invisible_set:
            inv_info = invisible_map.get(cp, {})
            sev = "high" if inv_info.get("category") == "bidi-control" else "medium"
            findings.append({
                "type": "invisible",
                "severity": sev,
                "character": ch,
                "codepoint": hex_cp,
                "position": idx,
                "name": inv_info.get("short_name", ""),
                "description": str(inv_info.get("risk", "")),
            })
            continue

        if cp in cp_to_skeleton:
            skeleton = cp_to_skeleton[cp]
            if skeleton and skeleton != ch:
                try:
                    import unicodedata
                    name = unicodedata.name(ch, "")
                except (ImportError, ValueError):
                    name = ""
                findings.append({
                    "type": "confusable",
                    "severity": "medium",
                    "character": ch,
                    "codepoint": hex_cp,
                    "position": idx,
                    "skeleton": skeleton,
                    "name": name,
                    "description": f"Looks like '{skeleton}' but is {name or 'unknown'}",
                })

    filtered = [f for f in findings if SEVERITY_RANK.get(str(f.get("severity", "low")), 0) >= min_sev]
    result["findings"] = filtered

    attack_vectors_out: list[dict[str, str]] = []
    text_lower = text.lower()
    for vec_name, vec_desc in ATTACK_VECTORS.items():
        for keyword in vec_desc.lower().split():
            if keyword in text_lower:
                attack_vectors_out.append({"vector": vec_name, "description": vec_desc})
                break
    result["attack_vectors"] = attack_vectors_out

    total = len(filtered)
    result["total_findings"] = total
    sev_counts: dict[str, int] = {}
    for f in filtered:
        sev = str(f["severity"])
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    result["severity_counts"] = sev_counts
    result["safe"] = total == 0

    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_language_detect_bom(args: argparse.Namespace) -> None:
    from general_ludd.language.charset_map import (
        BOM_OPTIONAL_BY_RFC,
        BOM_REQUIRED_BY_RFC,
        BOM_SIGNATURES,
    )

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    result: dict[str, object] = {"file": filepath}
    data: bytes | None = None

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except OSError as exc:
        result["error"] = str(exc)
        print(json.dumps(result, indent=2))
        return

    result["file_size"] = len(data)

    bom_found: str | None = None
    for name, sig in BOM_SIGNATURES.items():
        if data[: len(sig)] == sig:
            bom_found = name
            break

    if bom_found:
        result["bom_found"] = True
        result["bom_type"] = bom_found
        result["bom_encoding"] = bom_found.replace("_BOM", "").replace("_", "-")
        bom_size = len(BOM_SIGNATURES[bom_found])
        result["bom_size_bytes"] = bom_size
        result["bom_hex"] = " ".join(f"{b:02X}" for b in data[:bom_size])

        rfc_base = bom_found.replace("_BOM", "").replace("_", "-")
        if rfc_base in BOM_REQUIRED_BY_RFC:
            result["rfc_compliance"] = "required"
        elif rfc_base in BOM_OPTIONAL_BY_RFC:
            result["rfc_compliance"] = "optional"
        else:
            result["rfc_compliance"] = "none"

        if getattr(args, "strip", False):
            stripped = data[len(BOM_SIGNATURES[bom_found]):]
            result["stripped_preview"] = str(stripped[:100])
            with contextlib.suppress(LookupError, UnicodeDecodeError):
                result["stripped_text_preview"] = (
                    stripped[:200]
                    .decode(bom_found.replace("_BOM", "").replace("_", "-"),
                            errors="replace")
                )
    else:
        result["bom_found"] = False
        result["bom_type"] = None

    audit_dir: str = getattr(args, "audit_directory", "") or ""
    if audit_dir:
        audit_results: list[dict[str, object]] = []
        for dir_root, _dirs, files in os.walk(audit_dir):
            for fname in files:
                fpath = os.path.join(dir_root, fname)
                try:
                    with open(fpath, "rb") as f:
                        head = f.read(4)
                    found: str | None = None
                    for name, sig in BOM_SIGNATURES.items():
                        if head[: len(sig)] == sig:
                            found = name
                            break
                    audit_results.append({"file": fpath, "bom": found})
                except OSError:
                    pass
        result["audit_directory"] = audit_dir
        result["audit_file_count"] = len(audit_results)
        result["audit_results"] = audit_results

    print(json.dumps(result, indent=2))


_PHONETIC_METHODS = frozenset({"arpabet", "ipa", "soundex", "metaphone", "double_metaphone"})


def _cmd_language_phonetic_transcribe(args: argparse.Namespace) -> None:
    from general_ludd.language.phonetic_data import (
        ARPABET_TO_IPA,
        CMU_DICT_SUBSET,
        DOUBLE_METAPHONE,
        IPA_CONSONANTS,
        IPA_VOWELS,
        METAPHONE_EXCEPTIONS,
        SOUNDEX_MAPPING,
    )

    text = args.text or ""
    method = getattr(args, "method", "arpabet") or "arpabet"
    if method not in _PHONETIC_METHODS:
        print(f"Error: unknown method '{method}'", file=sys.stderr)
        sys.exit(1)

    if not text:
        result: dict[str, object] = {"input_text": "", "method": method, "words": []}
        print(json.dumps(result, indent=2))
        return

    import re
    WORD_RE = re.compile(r"[A-Za-z]+")
    words = WORD_RE.findall(text)

    def _arpabet_transcribe(word: str) -> list[str]:
        if word in CMU_DICT_SUBSET:
            return [CMU_DICT_SUBSET[word][0]]
        sounds = []
        for ch in word:
            for phoneme in IPA_CONSONANTS + IPA_VOWELS:
                if isinstance(phoneme, dict) and ch.lower() == phoneme.get("ipa", ""):
                    arpa = phoneme.get("arpabet", "")
                    if arpa and arpa not in sounds:
                        sounds.append(arpa)
        return [" ".join(sounds)] if sounds else [word]

    def _soundex(word: str) -> str:
        w = word.upper()
        if not w:
            return ""
        first = w[0]
        code = first
        prev = ""
        for ch in w[1:]:
            mapped = SOUNDEX_MAPPING.get(ch, "")
            if mapped and mapped != prev:
                code += mapped
                prev = mapped
        code = (code + "000")[:4]
        return code

    def _metaphone(word: str, double: bool = False) -> str | dict[str, str]:
        w = word.upper()
        if w in METAPHONE_EXCEPTIONS:
            exc = METAPHONE_EXCEPTIONS[w]
            if isinstance(exc, str):
                return exc
            if isinstance(exc, dict) and double:
                return exc
            return ""
        code = ""
        i = 0
        while i < len(w) and len(code) < 4:
            ch = w[i]
            if i == 0 and w.startswith(("KN", "GN", "PN", "AE", "WR")):
                i += 1
                continue
            if ch in "AEIOU":
                if i == 0:
                    code += ch
            elif ch == "B":
                code += "B"
            elif ch == "C":
                if i + 1 < len(w) and w[i + 1] in "IA":
                    code += "X"
                    i += 1
                elif i + 1 < len(w) and w[i + 1] == "H":
                    code += "X"
                elif i + 1 < len(w) and w[i + 1] == "K":
                    i += 1
                else:
                    code += "K"
            elif ch == "D":
                if i + 1 < len(w) and w[i + 1] in "GJ":
                    code += "J"
                    i += 1
                else:
                    code += "T"
            elif ch in "GJ":
                code += "J"
            elif ch in "PH":
                code += "F"
            elif ch == "Q":
                code += "K"
            elif ch in "SZ":
                if double:
                    code += "S"
                else:
                    code += "S"
            elif ch == "T":
                if i + 1 < len(w) and w[i + 1] == "H":
                    code += "0"
                    i += 1
                elif i + 1 < len(w) and w[i + 1] == "I" and i + 2 < len(w) and w[i + 2] in "AO":
                    code += "X"
                else:
                    code += "T"
            elif ch in "VW":
                code += ch
            elif ch == "X":
                code += "KS"
            elif ch in "LMRNF":
                code += ch
            i += 1
        return code[:4]

    phonetic_result: dict[str, object] = {
        "input_text": text,
        "method": method,
        "word_count": len(words),
        "words": [],
    }
    words_out: list[dict[str, object]] = phonetic_result["words"]  # type: ignore[assignment]

    for word in words:
        entry: dict[str, object] = {"word": word}
        if method == "arpabet":
            entry["transcription"] = _arpabet_transcribe(word)
        elif method == "ipa":
            arpa = _arpabet_transcribe(word)
            ipa_result = []
            for a in arpa:
                tokens = a.split()
                ipa_tokens = [ARPABET_TO_IPA.get(t, t) for t in tokens if t in ARPABET_TO_IPA]
                ipa_result.append("".join(ipa_tokens))
            entry["transcription"] = ipa_result
        elif method == "soundex":
            entry["transcription"] = _soundex(word)
            entry["soundex_code"] = entry["transcription"]
        elif method in ("metaphone", "double_metaphone"):
            mc = DOUBLE_METAPHONE.get(word, _metaphone(word, double=True))
            if method == "double_metaphone":
                entry["metaphone_codes"] = mc
                entry["transcription"] = mc.get("primary", "") if isinstance(mc, dict) else mc
            else:
                entry["transcription"] = mc.get("primary", "") if isinstance(mc, dict) else mc

        words_out.append(entry)

    print(json.dumps(phonetic_result, indent=2, ensure_ascii=False))


def _cmd_language_unicode_analyze(args: argparse.Namespace) -> None:
    import unicodedata

    from general_ludd.language.unicode_data import (
        UNICODE_BLOCK_NAMES,
        is_high_surrogate,
        is_low_surrogate,
        is_surrogate,
        plane_of,
    )

    raw = args.input
    string_mode = getattr(args, "string", False)

    if string_mode:
        chars_out: list[dict[str, object]] = []
        for ch in raw:
            cp_val = ord(ch)
            name = unicodedata.name(ch, "")
            chars_out.append({
                "character": ch,
                "codepoint": cp_val,
                "codepoint_hex": f"U+{cp_val:04X}",
                "name": name,
                "category": unicodedata.category(ch),
                "plane": plane_of(cp_val),
                "is_surrogate": is_surrogate(cp_val),
            })
        result_str: dict[str, object] = {
            "input": raw,
            "length": len(raw),
            "characters": chars_out,
        }
        print(json.dumps(result_str, indent=2, ensure_ascii=False))
        return

    cleaned = raw.strip()
    if cleaned.upper().startswith("U+"):
        cp = int(cleaned[2:], 16)
    elif cleaned.lower().startswith("0x"):
        cp = int(cleaned, 16)
    else:
        try:
            cp = int(cleaned)
        except ValueError:
            cp = ord(cleaned[0]) if cleaned else 0

    ch = chr(cp) if cp <= 0x10FFFF else ""
    name = unicodedata.name(ch, "") if ch else ""

    block_name = "Unknown"
    for (start, end), bname in UNICODE_BLOCK_NAMES.items():
        if start <= cp <= end:
            block_name = bname
            break

    utf8_bytes = b""
    utf16_bytes = b""
    utf32_bytes = b""
    if ch and not is_surrogate(cp):
        with contextlib.suppress(UnicodeEncodeError):
            utf8_bytes = ch.encode("utf-8")
        with contextlib.suppress(UnicodeEncodeError):
            utf16_bytes = ch.encode("utf-16-le")
        with contextlib.suppress(UnicodeEncodeError):
            utf32_bytes = ch.encode("utf-32-le")

    result = {
        "input": raw,
        "codepoint": cp,
        "codepoint_hex": f"U+{cp:04X}",
        "character": ch,
        "name": name,
        "category": unicodedata.category(ch) if ch else "",
        "plane": plane_of(cp),
        "block": block_name,
        "is_surrogate": is_surrogate(cp),
        "is_high_surrogate": is_high_surrogate(cp),
        "is_low_surrogate": is_low_surrogate(cp),
        "utf8_hex": " ".join(f"{b:02X}" for b in utf8_bytes),
        "utf16_hex": " ".join(f"{b:02X}" for b in utf16_bytes),
        "utf32_hex": " ".join(f"{b:02X}" for b in utf32_bytes),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_language_locale_format(args: argparse.Namespace) -> None:
    from general_ludd.language.locale_data import (
        evaluate_plural,
        format_currency,
        format_number,
        get_locale_data,
        negotiate_locale,
        parse_bcp47,
    )

    locale = args.locale
    result: dict[str, object] = {"locale": locale}

    number_val = getattr(args, "number", None)
    if number_val is not None:
        result["formatted"] = format_number(float(number_val), locale)
        result["type"] = "number"

    currency_args = getattr(args, "currency", None)
    if currency_args:
        amount = float(currency_args[0])
        code = currency_args[1] if len(currency_args) > 1 else "USD"
        result["formatted"] = format_currency(amount, code, locale)
        result["type"] = "currency"
        result["currency_code"] = code

    plural_val = getattr(args, "plural", None)
    if plural_val is not None:
        result["plural_category"] = evaluate_plural(locale, float(plural_val))

    available = getattr(args, "available", None)
    accept = getattr(args, "negotiate", None)
    if accept:
        avail_list = available.split(",") if available else [locale]
        result["negotiated"] = negotiate_locale(accept, avail_list, locale)

    if getattr(args, "info", False):
        data = get_locale_data(locale)
        if data:
            result["language_name"] = data["language_name"]
            result["script"] = data["script"]
            result["territory"] = data["territory"]
            result["is_rtl"] = data["is_rtl"]
            result["parsed"] = parse_bcp47(locale)
        else:
            result["error"] = "locale not found"

    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_language_i18n_extract(args: argparse.Namespace) -> None:
    from general_ludd.language.i18n_data import (
        extract_icu_placeholders,
        parse_po,
        pseudolocalize,
    )

    pseudo_text = getattr(args, "pseudolocalize", None)
    if pseudo_text is not None:
        method = getattr(args, "method", "accent") or "accent"
        result: dict[str, object] = {
            "input": pseudo_text,
            "method": method,
            "output": pseudolocalize(pseudo_text, method),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    icu_msg = getattr(args, "extract_icu", None)
    if icu_msg is not None:
        placeholders = extract_icu_placeholders(icu_msg)
        result = {
            "input": icu_msg,
            "placeholders": placeholders,
            "count": len(placeholders),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    po_file = getattr(args, "parse_po", None)
    if po_file:
        try:
            content = Path(po_file).read_text(encoding="utf-8")
            entries = parse_po(content)
            result = {
                "file": po_file,
                "entry_count": len(entries),
                "entries": entries,
            }
        except OSError as exc:
            result = {"file": po_file, "error": str(exc)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(json.dumps({"error": "no action specified"}, indent=2))


def _cmd_language_font_analyze(args: argparse.Namespace) -> None:
    import struct

    from general_ludd.language.font_data import (
        SYSTEM_FONT_STACKS,
        get_font_metrics,
        identify_font_format,
        list_font_tables,
    )

    if getattr(args, "system_stacks", False):
        result: dict[str, object] = {"stacks": SYSTEM_FONT_STACKS}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    font_path = args.file
    if not font_path or not os.path.isfile(font_path):
        print(f"Error: font file not found: {font_path}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(font_path)
    result: dict[str, object] = {
        "file": font_path,
        "file_size": file_size,
    }

    with open(font_path, "rb") as f:
        header = f.read(64)
    result["format"] = identify_font_format(header)

    if getattr(args, "tables", False):
        try:
            result["tables"] = [
                {"tag": t["tag"], "offset": t["offset"], "length": t["length"]}
                for t in list_font_tables(font_path)
            ]
        except (OSError, struct.error):
            result["tables"] = []

    if getattr(args, "metrics", False):
        try:
            m = get_font_metrics(font_path)
            if "error" in m:
                result["metrics_error"] = m["error"]
            else:
                result["metrics"] = m
        except (OSError, struct.error):
            result["metrics_error"] = "failed to read font header"

    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_language_analyze_text(args: argparse.Namespace) -> None:
    import unicodedata

    from general_ludd.language.homoglyph_data import (
        detect_bidi_overrides,
        detect_confusables,
        detect_invisible_chars,
        detect_mixed_script,
        generate_skeleton,
    )

    text = args.text or ""
    findings: list[dict[str, object]] = []

    for finding in detect_confusables(text):
        findings.append({
            "type": "confusable",
            "severity": "medium",
            "character": finding["character"],
            "codepoint": finding["codepoint"],
            "position": finding["position"],
            "skeleton": finding["skeleton"],
            "name": finding["name"],
        })

    for finding in detect_invisible_chars(text):
        sev = "high" if finding["category"] == "bidi-control" else "medium"
        findings.append({
            "type": "invisible",
            "severity": sev,
            "character": finding["character"],
            "codepoint": finding["codepoint"],
            "position": finding["position"],
            "name": finding["name"],
            "category": finding["category"],
        })

    for finding in detect_bidi_overrides(text):
        findings.append({
            "type": "bidi-override",
            "severity": "critical",
            "character": finding["character"],
            "codepoint": finding["codepoint"],
            "position": finding["position"],
            "name": finding["name"],
        })

    mixed = detect_mixed_script(text)
    skeleton = generate_skeleton(text)

    sev_counts: dict[str, int] = {}
    for f in findings:
        sev = str(f.get("severity", "low"))
        sev_counts[sev] = sev_counts.get(sev, 0) + 1

    result: dict[str, object] = {
        "input_length": len(text),
        "length": len(text),
        "grapheme_count": len(text),
        "total_findings": len(findings),
        "findings": findings,
        "severity_counts": sev_counts,
        "safe": len(findings) == 0,
        "mixed_script": {
            "is_mixed": mixed["is_mixed"],
            "scripts": mixed["scripts"],
            "counts": mixed["counts"],
        },
        "skeleton": skeleton,
        "is_nfc": unicodedata.is_normalized("NFC", text),
        "is_nfd": unicodedata.is_normalized("NFD", text),
        "is_nfkc": unicodedata.is_normalized("NFKC", text),
        "is_nfkd": unicodedata.is_normalized("NFKD", text),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def add_language_subparser(subparsers: Any) -> argparse.ArgumentParser:
    language_parser: argparse.ArgumentParser = subparsers.add_parser(
        "language",
        help="Language Expert operations (encoding, homoglyphs, BOM, phonetics, "
             "unicode, locale, i18n, fonts, text analysis)",
    )
    language_parser.set_defaults(func=None)
    lang_sub = language_parser.add_subparsers(dest="language_command")

    detect_enc = lang_sub.add_parser("detect-encoding", help="Detect character encoding of a file")
    detect_enc.add_argument("file", help="File to detect encoding for")
    detect_enc.add_argument("--detect-mojibake", action="store_true", default=False,
                            help="Check for mojibake patterns")
    detect_enc.add_argument("--list-encodings", action="store_true", default=False,
                            help="List all supported encodings")
    detect_enc.set_defaults(func=_cmd_language_detect_encoding)

    scan_homoglyphs = lang_sub.add_parser("scan-homoglyphs", help="Scan text for confusable/homoglyph characters")
    scan_homoglyphs.add_argument("text", help="Text to scan for homoglyphs")
    scan_homoglyphs.add_argument("--min-severity", default="low",
                                  choices=["low", "medium", "high", "critical"],
                                  help="Minimum severity threshold (default: low)")
    scan_homoglyphs.set_defaults(func=_cmd_language_scan_homoglyphs)

    detect_bom = lang_sub.add_parser("detect-bom", help="Detect and handle Byte Order Marks")
    detect_bom.add_argument("file", help="File to detect BOM for")
    detect_bom.add_argument("--strip", action="store_true", default=False,
                            help="Strip BOM from file (preview)")
    detect_bom.add_argument("--audit-directory", default="",
                            help="Directory to audit for BOMs across all files")
    detect_bom.set_defaults(func=_cmd_language_detect_bom)

    phonetic = lang_sub.add_parser("phonetic-transcribe", help="Convert text to phonetic representations")
    phonetic.add_argument("text", help="Text to transcribe phonetically")
    phonetic.add_argument("--method", default="arpabet",
                           choices=["arpabet", "ipa", "soundex", "metaphone", "double_metaphone"],
                           help="Transcription method (default: arpabet)")
    phonetic.set_defaults(func=_cmd_language_phonetic_transcribe)

    unicode_an = lang_sub.add_parser("unicode-analyze",
                                      help="Analyze Unicode properties of a codepoint or string")
    unicode_an.add_argument("input", help="Codepoint (U+XXXX, 0xXXXX, decimal) or string with --string")
    unicode_an.add_argument("--string", action="store_true", default=False,
                             help="Treat input as a string to analyze character-by-character")
    unicode_an.set_defaults(func=_cmd_language_unicode_analyze)

    locale_fmt = lang_sub.add_parser("locale-format",
                                      help="Format numbers/currency per locale, evaluate plurals, negotiate")
    locale_fmt.add_argument("locale", help="BCP 47 locale tag (e.g. en-US, de-DE)")
    locale_fmt.add_argument("--number", default=None, help="Number to format")
    locale_fmt.add_argument("--currency", nargs="+", default=None,
                             help="Currency: AMOUNT CODE (e.g. 99.50 USD)")
    locale_fmt.add_argument("--plural", default=None, help="Count to evaluate plural category")
    locale_fmt.add_argument("--negotiate", default=None,
                             help="Accept-Language header to negotiate")
    locale_fmt.add_argument("--available", default=None,
                             help="Comma-separated available locales for negotiation")
    locale_fmt.add_argument("--info", action="store_true", default=False,
                             help="Show locale metadata")
    locale_fmt.set_defaults(func=_cmd_language_locale_format)

    i18n_ext = lang_sub.add_parser("i18n-extract",
                                    help="Pseudolocalization, ICU extraction, .po parsing")
    i18n_ext.add_argument("--pseudolocalize", default=None,
                           help="Text to pseudolocalize")
    i18n_ext.add_argument("--method", default="accent",
                           choices=["accent", "bracket"],
                           help="Pseudolocalization method (default: accent)")
    i18n_ext.add_argument("--extract-icu", default=None,
                           help="ICU message to extract placeholders from")
    i18n_ext.add_argument("--parse-po", default=None,
                           help=".po file to parse")
    i18n_ext.set_defaults(func=_cmd_language_i18n_extract)

    font_an = lang_sub.add_parser("font-analyze",
                                   help="Analyze font files: format, tables, metrics, system stacks")
    font_an.add_argument("file", nargs="?", default=None,
                          help="Font file to analyze")
    font_an.add_argument("--tables", action="store_true", default=False,
                          help="List OpenType/TrueType tables")
    font_an.add_argument("--metrics", action="store_true", default=False,
                          help="Extract font metrics")
    font_an.add_argument("--system-stacks", action="store_true", default=False,
                          help="Print system font stacks per OS")
    font_an.set_defaults(func=_cmd_language_font_analyze)

    analyze_txt = lang_sub.add_parser("analyze-text",
                                       help="Comprehensive text health: homoglyphs, invisibles, bidi, mixed-script")
    analyze_txt.add_argument("text", help="Text to analyze")
    analyze_txt.set_defaults(func=_cmd_language_analyze_text)

    return language_parser
